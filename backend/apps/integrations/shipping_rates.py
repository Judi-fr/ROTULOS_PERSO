"""La tabla de tarifas de cada tienda (``ShippingRate``) y la regla con la que
se cotiza, comunes a todas las plataformas.

- **El precio sale de una tabla por tienda**, no de un cálculo ni de un
  transportista. Código postal de destino y peso del carrito entran, precio
  sale (``matching_rates``).
- **Un CP que no está en la tabla no se cotiza.** Se devuelve la lista sin
  esa opción en vez de inventar un precio: preferimos que el comerciante
  vea que le falta una zona a venderle un envío a pérdida.

Además de la tabla, la tienda puede ofrecer el precio de un transportista con
la cuenta de su dueño (hoy Andreani, ``apps.carriers.checkout``):
``with_carrier_rates`` las suma a las de la tabla y es lo que contestan los callbacks.

Cómo le llega esa respuesta a cada checkout es de cada plataforma: Tiendanube
nos llama como carrier (``providers/tiendanube/rates.py``), WooCommerce
pregunta desde nuestro plugin (``providers/woocommerce/rates.py``) y a VTEX
se le publica la tabla (``providers/vtex/freight.py``, abajo
``rates_changed``).
"""

from __future__ import annotations

from .models import ShippingRate

def normalize_postal_code(value):
    """Los dígitos comparables de un CP argentino.

    ``1602`` queda igual; un CPA ``C1602ABC`` da ``1602``. Se queda con los
    primeros cuatro dígitos que aparezcan: es lo único que existe tanto en
    el CP viejo como en el nuevo, y es lo que una tabla de zonas usa.
    Devuelve "" si no hay nada usable.
    """
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:4] if digits else ""


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


def with_carrier_rates(connection, rates, postal_code, weight_kg, cart_total=None):
    """Lo que se le ofrece al comprador: ``rates`` (las de la tabla, de
    ``matching_rates``) más las de los transportistas que la tienda activó
    (``ShippingRate`` sin guardar). Un transportista que falla no saca a la
    tabla del checkout. ``cart_total``: el total del carrito, si la plataforma
    lo manda (para el envío gratis desde cierto monto)."""
    from apps.carriers.checkout import carrier_rates

    taken = {rate.option_code for rate in rates}
    # Si la tabla ya usa ese código, gana la tabla: la plataforma no admite
    # dos tarifas con el mismo código.
    return list(rates) + [rate for rate in carrier_rates(connection, postal_code, weight_kg, cart_total) if rate.option_code not in taken]


def has_checkout_prices(connection):
    """Si la tienda tiene con qué cotizar: tarifas activas o un transportista
    activado. Sin nada, dar de alta el carrier dejaría una opción sin precio."""
    from apps.carriers.checkout import carrier_quotes_enabled

    return connection.shipping_rates.filter(is_active=True).exists() or carrier_quotes_enabled(connection)


# ---------------------------------------------------------------------------
# Plataformas que cotizan con una tabla propia (VTEX)
# ---------------------------------------------------------------------------
#
# Ahí no hay callback: la plataforma no nos pregunta el precio, así que la
# tabla se le publica (``StoreProvider.push_shipping_rates``) y se vuelve a
# publicar cuando cambia. Publicar es una decisión del comerciante (prende el
# envío en SU checkout), igual que registrar el carrier en Tiendanube: queda en
# ``preferences["rates_push"]["enabled"]`` y se cambia desde
# ``stores/<id>/publish-rates/``.

RATES_PUSH_PREF = "rates_push"


def rates_push_state(connection):
    """Lo que se sabe de la publicación: ``enabled``, ``status``
    (``pending``/``published``/``failed``), ``published_at``, ``rows``,
    ``unlinked_policies``, ``error``. ``{}`` = nunca se publicó."""
    return dict((connection.preferences or {}).get(RATES_PUSH_PREF) or {})


def save_rates_push_state(connection, **values):
    """Escribe con un UPDATE: el worker trae copias viejas de la conexión."""
    preferences = dict(connection.preferences or {})
    preferences[RATES_PUSH_PREF] = dict(preferences.get(RATES_PUSH_PREF) or {}, **values)
    connection.preferences = preferences
    type(connection).objects.filter(pk=connection.pk).update(preferences=preferences)


def enqueue_rates_push(connection):
    from .events import enqueue_event
    from .stores import PUSH_SHIPPING_RATES_EVENT

    save_rates_push_state(connection, status="pending")
    event, _ = enqueue_event(platform=connection.platform, event_type=PUSH_SHIPPING_RATES_EVENT, connection=connection)
    return event


def rates_changed(connection):
    """La tabla de ``connection`` (o un transportista de su checkout) cambió:
    si su plataforma necesita que se la publiquen y el comerciante la
    publicó, se vuelve a publicar; y se le avisa al proveedor
    (``checkout_prices_changed``). Varios cambios seguidos encolan un solo
    evento (el pendiente se reutiliza)."""
    from .providers import get_provider

    provider = get_provider(connection.platform)
    provider.checkout_prices_changed(connection)
    if provider.supports_rates_push and rates_push_state(connection).get("enabled"):
        return enqueue_rates_push(connection)
    return None
