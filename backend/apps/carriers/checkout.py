"""Precios de transportistas en el checkout de las tiendas: lo que
``apps.integrations.shipping_rates.with_carrier_rates`` suma a la tabla de tarifas.
Cada transportista vive en su carpeta (``andreani/checkout.py``); acá solo se
los junta, y uno que falla no rompe a los demás ni al checkout."""

from __future__ import annotations

import logging

from .andreani import checkout as andreani_checkout

logger = logging.getLogger(__name__)

CARRIERS = (andreani_checkout,)


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
