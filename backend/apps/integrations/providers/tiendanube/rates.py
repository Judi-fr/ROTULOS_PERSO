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

from ...shipping_rates import matching_rates, normalize_postal_code

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


def quote(connection, payload):
    """El cuerpo de la respuesta al checkout: ``{"rates": [...]}``.

    Una lista vacía es una respuesta válida y quiere decir "no llegamos a
    ese destino": la plataforma simplemente no muestra nuestra opción.
    """
    postal_code = destination_postal_code(payload)
    weight_kg = cart_weight_kg(payload)
    rates = matching_rates(connection, postal_code, weight_kg)

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

    return {
        "rates": [
            {
                "name": rate.option_name,
                "code": rate.option_code,
                "price": float(rate.price),
                "price_merchant": float(rate.price),
                "currency": rate.currency,
                "type": RATE_TYPE_SHIP,
                **_delivery_dates(rate),
            }
            for rate in rates
        ]
    }
