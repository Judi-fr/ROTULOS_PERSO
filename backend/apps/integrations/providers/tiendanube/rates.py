"""Cotización del checkout de Tiendanube: el callback que la plataforma
llama en cada checkout de una tienda que nos tiene como carrier, y el formato
en que se le contesta. La tabla y la regla son las comunes
(``apps.integrations.shipping_rates.matching_rates``).

- **Está en el camino de una venta ajena.** Si no contestamos, el comprador
  no ve nuestra opción de envío; si fallamos seguido, Tiendanube abre un
  corta-corriente (500 pedidos en 30 minutos con 50% de error) y deja de
  preguntarnos por 5 minutos. Por eso acá no se llama a ninguna API ni se
  encola nada: es una consulta a la base y se contesta (``rate_views``).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from ...shipping_rates import matching_rates, normalize_postal_code, with_carrier_rates

logger = logging.getLogger(__name__)

# Tipo de envío de la plataforma: a un domicilio ("ship") o a retirar
# ("pickup"). Hoy solo cotizamos a domicilio: no tenemos sucursales.
RATE_TYPE_SHIP = "ship"


def _setting(name, default):
    return getattr(settings, name, default)


def rates_callback_url(connection):
    """La URL que se registra como ``callback_url`` de esta tienda: donde la
    plataforma nos pregunta los precios. Vacía si falta
    ``INTEGRATIONS_PUBLIC_BASE_URL``.

    Lleva el mismo token firmado que el callback de rótulos, porque el
    payload del checkout tampoco dice de qué tienda es (trae ``store_id``,
    pero venir en el cuerpo no lo hace confiable).
    """
    from . import labels as store_labels

    base = str(_setting("INTEGRATIONS_PUBLIC_BASE_URL", "") or "").rstrip("/")
    if not base:
        return ""
    path = reverse(
        "tiendanube-rates", kwargs={"token": store_labels.make_callback_token(connection)}
    )
    return f"{base}{path}"


# ---------------------------------------------------------------------------
# Lo que manda la plataforma
# ---------------------------------------------------------------------------


def destination_postal_code(payload):
    """CP del comprador, normalizado. "" si el carrito no lo trae."""
    destination = payload.get("destination") if isinstance(payload, dict) else None
    if not isinstance(destination, dict):
        return ""
    return normalize_postal_code(destination.get("postal_code"))


def cart_weight_kg(payload):
    """Peso total del carrito en kilos.

    La plataforma manda ``grams`` por ítem (no kilos) y la cantidad aparte.
    Un ítem sin peso suma cero: es preferible cotizar de menos que no
    cotizar, y el comerciante que no carga pesos ya sabe lo que hace.
    """
    total = Decimal("0")
    items = payload.get("items") if isinstance(payload, dict) else None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            grams = Decimal(str(item.get("grams") or 0))
            quantity = Decimal(str(item.get("quantity") or 1))
        except (InvalidOperation, ValueError):
            continue
        total += grams * quantity
    return total / Decimal("1000")


def cart_total(payload):
    """El total del carrito (``total_price``; si no viene, la suma de
    ``price`` x ``quantity`` de los ítems). ``None`` si no hay con qué
    calcularlo: sin total no se aplica un envío gratis."""
    if not isinstance(payload, dict):
        return None
    try:
        if payload.get("total_price") not in (None, ""):
            total = Decimal(str(payload["total_price"]))
            return total if total.is_finite() else None
        items = [item for item in payload.get("items") or [] if isinstance(item, dict) and item.get("price") not in (None, "")]
        if not items:
            return None
        return sum((Decimal(str(item["price"])) * Decimal(str(item.get("quantity") or 1)) for item in items), Decimal("0"))
    except (InvalidOperation, ValueError):
        return None


def _delivery_dates(rate):
    """Las fechas ISO que espera la plataforma, a partir de los días
    prometidos. Sin días cargados no se manda nada: es mejor no prometer
    una fecha que prometer una inventada."""
    dates = {}
    now = timezone.now()
    if rate.delivery_days_min is not None:
        dates["min_delivery_date"] = (now + timedelta(days=rate.delivery_days_min)).isoformat()
    if rate.delivery_days_max is not None:
        dates["max_delivery_date"] = (now + timedelta(days=rate.delivery_days_max)).isoformat()
    return dates


def _log_codes_without_option(connection, payload, rates):
    """La plataforma descarta toda tarifa cuyo ``code`` no tenga una opción
    activa del carrier (``carrier_options``), sin avisar. El carrito trae las
    opciones de la tienda en ``carrier.options``: si falta alguna, queda en el
    log, que es lo único que explica una opción que no aparece."""
    carrier = payload.get("carrier") if isinstance(payload, dict) else None
    options = carrier.get("options") if isinstance(carrier, dict) else None
    if not isinstance(options, list):
        return
    known = {str(option.get("code")) for option in options if isinstance(option, dict)}
    missing = sorted({rate.option_code for rate in rates} - known)
    if missing:
        logger.warning(
            "Tienda %s: el carrier no tiene opción para %s; Tiendanube descarta esas tarifas.",
            connection.pk,
            ", ".join(missing),
        )


def quote(connection, payload):
    """El cuerpo de la respuesta al checkout: ``{"rates": [...]}``.

    Una lista vacía es una respuesta válida y quiere decir "no llegamos a
    ese destino": la plataforma simplemente no muestra nuestra opción.
    """
    postal_code = destination_postal_code(payload)
    weight_kg = cart_weight_kg(payload)
    rates = with_carrier_rates(
        connection, matching_rates(connection, postal_code, weight_kg), postal_code, weight_kg, cart_total(payload)
    )

    if not rates:
        # Un destino sin tarifa es el agujero más común de una tabla recién
        # cargada; queda en el log para que se pueda completar.
        logger.info(
            "Sin tarifa para la tienda %s: CP %r, %s kg.",
            connection.pk,
            postal_code,
            weight_kg,
        )
        return {"rates": []}

    _log_codes_without_option(connection, payload, rates)
    return {
        "rates": [
            {
                "name": rate.option_name,
                "code": rate.option_code,
                "price": float(rate.price),
                # Lo que paga la tienda: distinto de ``price`` con un recargo o
                # un envío gratis de un transportista (``merchant_price``).
                "price_merchant": float(getattr(rate, "merchant_price", rate.price)),
                "currency": rate.currency,
                "type": RATE_TYPE_SHIP,
                **_delivery_dates(rate),
            }
            for rate in rates
        ]
    }
