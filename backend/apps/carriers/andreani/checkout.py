"""El precio de Andreani en el checkout de la tienda.

En las plataformas que nos preguntan el precio en vivo (Tiendanube como
carrier, WooCommerce desde nuestro plugin: ``StoreProvider.quotes_at_checkout``),
además de la tabla de tarifas de la tienda se puede ofrecer una opción
"Andreani" cotizada con el cotizador de Andreani (``GET /v1/tarifas``) y la
cuenta del DUEÑO de la tienda — nunca una cuenta nuestra.

- **Se enciende por tienda** (``preferences["andreani_checkout"]``:
  ``enabled``, ``contract``, ``name``, ``delivery_days_min/max``), desde
  ``checkout_andreani.html``. Solo contratos a domicilio: la entrega en
  sucursal necesitaría que el comprador elija la sucursal en el checkout.
- **Está en el camino de una venta ajena**, así que: tiempo de espera corto
  (``ANDREANI_CHECKOUT_TIMEOUT_SECONDS``), la respuesta se guarda en caché
  (``ANDREANI_CHECKOUT_CACHE_SECONDS``, por contrato + CP + peso) y una falla
  también, un rato más corto (``ANDREANI_CHECKOUT_FAILURE_CACHE_SECONDS``),
  para que con Andreani caída no se espere el tiempo entero en cada checkout.
  Si falla, la opción simplemente no aparece: las de la tabla siguen.
- **Lo que paga el comprador** sale del total CON IVA de Andreani más el
  recargo de la tienda (``surcharge_percent`` y/o ``surcharge_amount``), o
  cero cuando el carrito llega a ``free_shipping_from`` (envío gratis: lo paga
  el comercio). El costo real queda en ``merchant_price`` de la tarifa, que
  Tiendanube recibe como ``price_merchant``. El total del carrito lo manda la
  plataforma (Tiendanube ``total_price``, el plugin de WooCommerce
  ``cart_total`` desde la 1.2.0); si no viene, no hay envío gratis.
"""

from __future__ import annotations

import hashlib
import logging
from decimal import ROUND_UP, Decimal

from django.conf import settings
from django.core.cache import cache

from ..models import CarrierAccount
from .client import AndreaniClient, AndreaniError
from .shipments import ShipmentError, postal_code_digits

logger = logging.getLogger(__name__)

PREF = "andreani_checkout"
OPTION_CODE = "andreani"
DEFAULT_NAME = "Andreani a domicilio"
# Peso redondeado hacia arriba a esta fracción de kilo para la clave de la
# caché: 1,23 kg y 1,27 kg cotizan igual (Andreani cobra por franja), y se
# cotiza con el peso redondeado para no cobrar de menos.
WEIGHT_STEP = Decimal("0.5")
_FAILED = "failed"


def _setting(name, default):
    return getattr(settings, name, default)


def config(connection):
    """La configuración de la tienda (dict; ``{}`` = nunca se configuró)."""
    return dict((connection.preferences or {}).get(PREF) or {})


def save_config(connection, values):
    """Escribe con un UPDATE: el worker tiene copias viejas de la conexión y
    un ``save()`` pisaría sus ``preferences``."""
    preferences = dict(connection.preferences or {})
    preferences[PREF] = values
    connection.preferences = preferences
    type(connection).objects.filter(pk=connection.pk).update(preferences=preferences)


def account_for(connection):
    """La cuenta de Andreani del dueño de la tienda, si está activa."""
    if not connection.owner_id:
        return None
    return CarrierAccount.objects.filter(
        owner_id=connection.owner_id, carrier=CarrierAccount.Carrier.ANDREANI, is_active=True
    ).first()


def problems(connection, account=None, settings_values=None):
    """Por qué esta tienda no puede cotizar con Andreani (lista de textos)."""
    values = config(connection) if settings_values is None else settings_values
    account = account if account is not None else account_for(connection)
    found = []
    if account is None:
        found.append("no tenés una cuenta de Andreani activa")
        return found
    if not account.username or not account.password_encrypted:
        found.append("a tu cuenta de Andreani le falta el usuario y la contraseña")
    if not account.client_code:
        found.append("a tu cuenta de Andreani le falta el código de cliente")
    contract = account.contract(values.get("contract")) if values.get("contract") else None
    if contract is None:
        found.append("elegí un contrato de tu cuenta")
    elif contract.get("kind") != "home":
        found.append("el contrato tiene que ser de entrega a domicilio")
    return found


def _rounded_weight(weight_kg, account):
    weight = Decimal(str(weight_kg or 0))
    if weight <= 0:
        # El carrito sin pesos cargados: se cotiza el paquete por defecto de la
        # cuenta, el mismo que se manda al crear el envío.
        weight = Decimal(account.default_weight_kg)
    steps = (weight / WEIGHT_STEP).to_integral_value(rounding=ROUND_UP)
    return max(steps, 1) * WEIGHT_STEP


def _cache_key(account, contract, postal_code, weight):
    raw = f"{account.pk}:{account.environment}:{account.client_code}:{contract}:{postal_code}:{weight}:{account.default_volume_cm3}"
    return "andreani-checkout:" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def quote_price(account, contract, postal_code, weight_kg):
    """El total con IVA (``Decimal``) para ese destino y peso, o ``None`` si
    Andreani no cotiza. Pasa por la caché; nunca lanza ``AndreaniError``."""
    weight = _rounded_weight(weight_kg, account)
    key = _cache_key(account, contract, postal_code, weight)
    cached = cache.get(key)
    if cached == _FAILED:
        return None
    if cached is not None:
        return Decimal(cached)
    client = AndreaniClient(account, timeout=_setting("ANDREANI_CHECKOUT_TIMEOUT_SECONDS", 4))
    try:
        data = client.quote(
            contract=contract,
            client_code=account.client_code,
            postal_code=postal_code,
            packages=[{"volumen": int(account.default_volume_cm3), "kilos": float(weight)}],
        )
        price = Decimal(str((data.get("tarifaConIva") or {}).get("total")).replace(",", "."))
        if not price.is_finite() or price <= 0:
            raise AndreaniError("Andreani devolvió una tarifa vacía.")
    except (AndreaniError, ArithmeticError, ValueError) as exc:
        logger.info("Andreani no cotizó el checkout (cuenta %s, CP %s, %s kg): %s", account.pk, postal_code, weight, exc)
        cache.set(key, _FAILED, _setting("ANDREANI_CHECKOUT_FAILURE_CACHE_SECONDS", 120))
        return None
    price = price.quantize(Decimal("0.01"))
    cache.set(key, str(price), _setting("ANDREANI_CHECKOUT_CACHE_SECONDS", 1800))
    return price


def _decimal_or_none(value):
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return number if number.is_finite() else None


def buyer_price(cost, values, cart_total=None):
    """Lo que paga el comprador por un envío que a la tienda le cuesta
    ``cost``, con su recargo y su envío gratis."""
    free_from = _decimal_or_none(values.get("free_shipping_from"))
    if free_from is not None and cart_total is not None and cart_total >= free_from:
        return Decimal("0.00")
    percent = _decimal_or_none(values.get("surcharge_percent")) or Decimal("0")
    amount = _decimal_or_none(values.get("surcharge_amount")) or Decimal("0")
    price = cost * (Decimal("1") + percent / Decimal("100")) + amount
    return max(price, Decimal("0")).quantize(Decimal("0.01"))


def checkout_rate(connection, postal_code, weight_kg, cart_total=None):
    """La opción de Andreani para el checkout de ``connection``, como un
    ``ShippingRate`` sin guardar (las plataformas ya saben contestar con
    eso), o ``None`` si la tienda no la ofrece o no hay precio."""
    values = config(connection)
    if not values.get("enabled"):
        return None
    postal_code = postal_code_digits(postal_code)
    if not postal_code:
        return None
    account = account_for(connection)
    if account is None or problems(connection, account, values):
        return None
    cost = quote_price(account, str(values["contract"]), postal_code, weight_kg)
    if cost is None:
        return None
    from apps.integrations.models import ShippingRate

    rate = ShippingRate(
        connection=connection,
        option_code=OPTION_CODE,
        option_name=values.get("name") or DEFAULT_NAME,
        postal_code_from=postal_code,
        postal_code_to=postal_code,
        price=buyer_price(cost, values, cart_total),
        currency="ARS",
        delivery_days_min=values.get("delivery_days_min"),
        delivery_days_max=values.get("delivery_days_max"),
    )
    # Lo que le cuesta a la tienda (no es un campo del modelo: la tarifa no se guarda).
    rate.merchant_price = cost
    return rate


def test_quote(connection, postal_code, weight_kg, cart_total=None):
    """Para el botón "Probar": el precio sin caché, o ``ShipmentError`` con
    el motivo en palabras del comerciante."""
    values = config(connection)
    account = account_for(connection)
    found = problems(connection, account, values)
    if found:
        raise ShipmentError("No se puede cotizar: " + "; ".join(found) + ".")
    postal_code = postal_code_digits(postal_code)
    if not postal_code:
        raise ShipmentError("Indicá un código postal de destino.")
    weight = _rounded_weight(weight_kg, account)
    try:
        data = AndreaniClient(account).quote(
            contract=str(values["contract"]),
            client_code=account.client_code,
            postal_code=postal_code,
            packages=[{"volumen": int(account.default_volume_cm3), "kilos": float(weight)}],
        )
    except AndreaniError as exc:
        raise ShipmentError(str(exc)) from exc
    cost = _decimal_or_none((data.get("tarifaConIva") or {}).get("total"))
    if cost is None:
        raise ShipmentError("Andreani no devolvió el precio del envío.")
    cost = cost.quantize(Decimal("0.01"))
    return {
        "cost": str(cost),
        "price": str(buyer_price(cost, values, cart_total)),
        "weight_kg": str(weight),
        "postal_code": postal_code,
    }
