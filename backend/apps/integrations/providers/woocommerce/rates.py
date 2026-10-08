"""Cotización del envío en el checkout de WooCommerce.

Lo mismo que ``shipping_rates`` hace para Tiendanube, del lado de
WooCommerce: allá la plataforma nos llama como carrier registrado; acá no hay
API de carriers, así que el que pregunta es el método de envío de nuestro
plugin de WordPress ("Rótulos de envío"), que el comerciante agrega a sus
zonas de envío. El plugin manda el CP de destino y el peso del carrito ya en
kilos (WooCommerce deja cargar pesos en g, kg, lbs u oz: la conversión la
hace WordPress con ``wc_get_weight``), firmado con el secreto de la tienda.

La tabla y la regla son las mismas (``shipping_rates.matching_rates``): un CP
fuera de la tabla no se cotiza, y de cada modalidad gana la franja de peso
más ajustada que alcance.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from ...shipping_rates import matching_rates, normalize_postal_code

logger = logging.getLogger(__name__)


def cart_weight_kg(data):
    """Peso del carrito en kilos, tal cual lo manda el plugin. Inválido o
    negativo cuenta como cero, como un ítem sin peso en Tiendanube: es
    preferible cotizar de menos que no cotizar."""
    try:
        weight = Decimal(str(data.get("weight_kg") or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    return weight if weight.is_finite() and weight > 0 else Decimal("0")


def quote(connection, data):
    """``{"rates": [...]}`` para el plugin: cada tarifa con su código,
    nombre, precio, moneda y plazo en días. Lista vacía = no llegamos a ese
    destino (el checkout muestra las demás opciones de la tienda)."""
    country = str(data.get("country") or "").strip().upper()
    postal_code = normalize_postal_code(data.get("postcode"))
    weight_kg = cart_weight_kg(data)
    # Los CP de la tabla son argentinos (ver normalize_postal_code): un
    # destino en otro país no se cotiza aunque sus dígitos coincidan.
    if country and country != "AR":
        return {"rates": []}

    rates = matching_rates(connection, postal_code, weight_kg)
    if not rates:
        logger.info("Sin tarifa para la tienda %s: CP %r, %s kg.", connection.pk, postal_code, weight_kg)
    return {
        "rates": [
            {
                "code": rate.option_code,
                "name": rate.option_name,
                "price": str(rate.price),
                "currency": rate.currency,
                "delivery_days_min": rate.delivery_days_min,
                "delivery_days_max": rate.delivery_days_max,
            }
            for rate in rates
        ]
    }
