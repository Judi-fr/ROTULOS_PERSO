"""Precios de transportistas en el checkout de las tiendas: lo que
``apps.integrations.shipping_rates.with_carrier_rates`` suma a la tabla de tarifas.

En las plataformas que nos preguntan el precio en vivo (Tiendanube como
carrier, WooCommerce desde nuestro plugin: ``StoreProvider.quotes_at_checkout``)
cada tienda puede ofrecer una opción por transportista, cotizada con la cuenta
del DUEÑO de la tienda — nunca una cuenta nuestra. Andreani tiene su módulo
propio (``andreani/checkout.py``, anterior a esta capa); Correo Argentino y OCA
usan ``CarrierCheckout``, que hace lo mismo para cualquier proveedor con
``supports_checkout`` a través de su ``checkout_quote``. Los tres tienen la misma
forma (``OPTION_CODE``, ``DEFAULT_NAME``, ``config``, ``save_config``,
``account_for``, ``problems``, ``checkout_rate``, ``test_quote``), así que las
vistas y el callback no distinguen.

- **Se enciende por tienda y por transportista** (``preferences["<código>_checkout"]``:
  ``enabled``, ``contract`` — solo a domicilio: la sucursal la tendría que
  elegir el comprador —, ``name``, ``delivery_days_min/max``, recargo y envío
  gratis), desde ``checkout_transportistas.html``.
- **Está en el camino de una venta ajena:** espera poco
  (``CARRIER_CHECKOUT_TIMEOUT_SECONDS``), guarda las respuestas por cuenta +
  contrato + CP + peso redondeado (``CARRIER_CHECKOUT_CACHE_SECONDS``) y también
  las fallas, un rato más corto (``CARRIER_CHECKOUT_FAILURE_CACHE_SECONDS``).
  Si falla, la opción no aparece y las demás siguen. Uno que falla no rompe a
  los demás ni al checkout.
"""

from __future__ import annotations

import hashlib
import logging
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache

from . import common
from .andreani import checkout as andreani_checkout
from .base import CarrierError, ShipmentError
from .models import CarrierAccount
from .pricing import buyer_price, decimal_or_none, rounded_weight
from .providers import all_providers

logger = logging.getLogger(__name__)

_FAILED = "failed"


def _setting(name, default):
    return getattr(settings, name, default)


class CarrierCheckout:
    """El precio de un transportista en el checkout, para cualquier proveedor
    con ``checkout_quote``."""

    def __init__(self, provider):
        self.provider = provider
        self.OPTION_CODE = provider.code
        self.PREF = f"{provider.code}_checkout"
        self.DEFAULT_NAME = provider.checkout_default_name or f"{provider.name} a domicilio"

    def config(self, connection):
        """La configuración de la tienda (dict; ``{}`` = nunca se configuró)."""
        return dict((connection.preferences or {}).get(self.PREF) or {})

    def save_config(self, connection, values):
        """Con un UPDATE: el worker tiene copias viejas de la conexión y un
        ``save()`` pisaría sus ``preferences``."""
        preferences = dict(connection.preferences or {})
        preferences[self.PREF] = values
        connection.preferences = preferences
        type(connection).objects.filter(pk=connection.pk).update(preferences=preferences)

    def account_for(self, connection):
        """La cuenta del dueño de la tienda en este transportista, si está activa."""
        if not connection.owner_id:
            return None
        return CarrierAccount.objects.filter(owner_id=connection.owner_id, carrier=self.provider.code, is_active=True).first()

    def problems(self, connection, account=None, settings_values=None):
        """Por qué esta tienda no puede cotizar con este transportista (textos)."""
        values = self.config(connection) if settings_values is None else settings_values
        account = account if account is not None else self.account_for(connection)
        name = self.provider.name
        if account is None:
            return [f"no tenés una cuenta de {name} activa"]
        found = [f"a tu cuenta de {name} le falta {item}" for item in self.provider.credential_problems(account, "quote")]
        found += self.provider.checkout_problems(account)
        contract = account.contract(values.get("contract")) if values.get("contract") else None
        if contract is None:
            found.append(f"elegí {self.provider.contract_one} de tu cuenta")
        elif contract.get("kind") != "home":
            found.append(f"{self.provider.contract_one} tiene que ser de entrega a domicilio")
        return found

    def _cache_key(self, account, contract, postal_code, weight):
        box = common.box_cm(account)
        raw = f"{self.provider.code}:{account.pk}:{account.environment}:{contract}:{postal_code}:{weight}:{box}:{account.default_volume_cm3}"
        return "carrier-checkout:" + hashlib.sha256(raw.encode()).hexdigest()[:32]

    def quote_price(self, account, contract, postal_code, weight_kg):
        """El costo (``Decimal``) para ese destino y peso, o ``None``. Pasa por
        la caché y nunca lanza: está en medio de una venta."""
        weight = rounded_weight(weight_kg, account)
        key = self._cache_key(account, contract["code"], postal_code, weight)
        cached = cache.get(key)
        if cached == _FAILED:
            return None
        if cached is not None:
            return decimal_or_none(cached)
        try:
            price = self.provider.checkout_quote(
                account, contract, postal_code, weight, timeout=_setting("CARRIER_CHECKOUT_TIMEOUT_SECONDS", 4)
            )
            if price is None or not price.is_finite() or price <= 0:
                raise CarrierError(f"{self.provider.name} devolvió una tarifa vacía.")
        except (CarrierError, ShipmentError, ArithmeticError, ValueError) as exc:
            logger.info(
                "%s no cotizó el checkout (cuenta %s, CP %s, %s kg): %s", self.provider.name, account.pk, postal_code, weight, exc
            )
            cache.set(key, _FAILED, _setting("CARRIER_CHECKOUT_FAILURE_CACHE_SECONDS", 120))
            return None
        price = price.quantize(Decimal("0.01"))
        cache.set(key, str(price), _setting("CARRIER_CHECKOUT_CACHE_SECONDS", 1800))
        return price

    def checkout_rate(self, connection, postal_code, weight_kg, cart_total=None):
        """La opción para el checkout de ``connection`` como un ``ShippingRate``
        sin guardar, o ``None`` si la tienda no la ofrece o no hay precio."""
        values = self.config(connection)
        if not values.get("enabled"):
            return None
        postal_code = common.postal_code_digits(postal_code)
        if not postal_code:
            return None
        account = self.account_for(connection)
        if account is None or self.problems(connection, account, values):
            return None
        cost = self.quote_price(account, account.contract(values["contract"]), postal_code, weight_kg)
        if cost is None:
            return None
        from apps.integrations.models import ShippingRate

        rate = ShippingRate(
            connection=connection,
            option_code=self.OPTION_CODE,
            option_name=values.get("name") or self.DEFAULT_NAME,
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

    def test_quote(self, connection, postal_code, weight_kg, cart_total=None):
        """Para el botón "Probar": el precio sin caché, o ``ShipmentError`` con
        el motivo en palabras del comerciante."""
        values = self.config(connection)
        account = self.account_for(connection)
        found = self.problems(connection, account, values)
        if found:
            raise ShipmentError("No se puede cotizar: " + "; ".join(found) + ".")
        postal_code = common.postal_code_digits(postal_code)
        if not postal_code:
            raise ShipmentError("Indicá un código postal de destino.")
        weight = rounded_weight(weight_kg, account)
        try:
            cost = self.provider.checkout_quote(account, account.contract(values["contract"]), postal_code, weight)
        except CarrierError as exc:
            raise ShipmentError(str(exc)) from exc
        if cost is None:
            raise ShipmentError(f"{self.provider.name} no devolvió el precio del envío.")
        cost = cost.quantize(Decimal("0.01"))
        return {"cost": str(cost), "price": str(buyer_price(cost, values, cart_total)), "weight_kg": str(weight), "postal_code": postal_code}


# Andreani con su módulo propio; los demás, con la clase común.
CARRIERS = (andreani_checkout,) + tuple(
    CarrierCheckout(provider) for provider in all_providers() if provider.code != andreani_checkout.OPTION_CODE and provider.supports_checkout
)


def checkout_for(code):
    """El precio en el checkout del transportista ``code``, o ``None``."""
    return next((carrier for carrier in CARRIERS if carrier.OPTION_CODE == code), None)


def carrier_rates(connection, postal_code, weight_kg, cart_total=None):
    """``cart_total`` (``Decimal`` o ``None``) es para el envío gratis desde
    cierto monto."""
    rates = []
    for carrier in CARRIERS:
        try:
            rate = carrier.checkout_rate(connection, postal_code, weight_kg, cart_total)
        except Exception:  # noqa: BLE001 - nunca romper un checkout ajeno
            logger.exception("Error cotizando %s para la tienda %s.", carrier.OPTION_CODE, connection.pk)
            continue
        if rate is not None:
            rates.append(rate)
    return rates


def carrier_quotes_enabled(connection):
    return any(carrier.config(connection).get("enabled") for carrier in CARRIERS)
