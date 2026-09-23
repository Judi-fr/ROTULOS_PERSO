"""Cotización de envíos para el checkout de la tienda.

La otra mitad de ser un medio de envío. ``store_labels`` resuelve el rótulo
DESPUÉS de la venta; esto resuelve el precio ANTES: cuando un comprador
llega al checkout de una tienda que nos tiene como carrier, la plataforma
nos manda el carrito y espera la lista de tarifas que ese comprador va a
ver como opción de envío.

Tres cosas que lo hacen distinto del resto del código:

- **Está en el camino de una venta ajena.** Si no contestamos, el comprador
  no ve nuestra opción de envío; si fallamos seguido, Tiendanube abre un
  corta-corriente (500 pedidos en 30 minutos con 50% de error) y deja de
  preguntarnos por 5 minutos. Por eso acá no se llama a ninguna API ni se
  encola nada: es una consulta a la base y se contesta.
- **El precio sale de una tabla por tienda** (``ShippingRate``), no de un
  cálculo ni de un transportista. Código postal de destino y peso del
  carrito entran, precio sale.
- **Un CP que no está en la tabla no se cotiza.** Se devuelve la lista sin
  esa opción en vez de inventar un precio: preferimos que el comerciante
  vea que le falta una zona a venderle un envío a pérdida.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .models import ShippingRate

logger = logging.getLogger(__name__)

# Tipo de envío de la plataforma: a un domicilio ("ship") o a retirar
# ("pickup"). Hoy solo cotizamos a domicilio: no tenemos sucursales.
RATE_TYPE_SHIP = "ship"


def _setting(name, default):
    return getattr(settings, name, default)


def normalize_postal_code(value):
    """Los dígitos comparables de un CP argentino.

    ``1602`` queda igual; un CPA ``C1602ABC`` da ``1602``. Se queda con los
    primeros cuatro dígitos que aparezcan: es lo único que existe tanto en
    el CP viejo como en el nuevo, y es lo que una tabla de zonas usa.
    Devuelve "" si no hay nada usable.
    """
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:4] if digits else ""


def rates_callback_url(connection):
    """La URL que se registra como ``callback_url`` de esta tienda: donde la
    plataforma nos pregunta los precios. Vacía si falta
    ``INTEGRATIONS_PUBLIC_BASE_URL``.

    Lleva el mismo token firmado que el callback de rótulos, porque el
    payload del checkout tampoco dice de qué tienda es (trae ``store_id``,
    pero venir en el cuerpo no lo hace confiable).
    """
    from . import store_labels

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


# ---------------------------------------------------------------------------
# La tabla
# ---------------------------------------------------------------------------


def matching_rates(connection, postal_code, weight_kg):
    """Las tarifas que aplican a ese destino y ese peso, una por modalidad.

    De cada ``option_code`` se elige la franja de menor techo que alcance
    el peso: con franjas de 1, 5 y 25 kg, un paquete de 3 kg paga la de 5.
    Si ninguna franja le llega, esa modalidad no se ofrece — mandar la de
    25 kg sería cobrarle de más, y la de 1 kg, de menos.
    """
    if not postal_code:
        return []

    candidates = ShippingRate.objects.filter(
        connection=connection,
        is_active=True,
        postal_code_from__lte=postal_code,
        postal_code_to__gte=postal_code,
    ).order_by("option_code", "weight_up_to_kg")

    best = {}
    for rate in candidates:
        # null = franja sin tope: cubre cualquier peso.
        if rate.weight_up_to_kg is not None and weight_kg > rate.weight_up_to_kg:
            continue
        current = best.get(rate.option_code)
        if current is None:
            best[rate.option_code] = rate
            continue
        # Entre dos franjas que alcanzan, gana la más ajustada; una sin
        # tope solo gana si no hay ninguna con tope que sirva.
        if current.weight_up_to_kg is None and rate.weight_up_to_kg is not None:
            best[rate.option_code] = rate
        elif (
            current.weight_up_to_kg is not None
            and rate.weight_up_to_kg is not None
            and rate.weight_up_to_kg < current.weight_up_to_kg
        ):
            best[rate.option_code] = rate
    return [best[code] for code in sorted(best)]


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
