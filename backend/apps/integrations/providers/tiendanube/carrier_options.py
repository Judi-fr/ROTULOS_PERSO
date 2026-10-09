"""Las opciones de nuestro carrier en la tienda (``/shipping_carriers/{id}/options``).

Tiendanube no muestra lo que contestamos tal cual: "you have to post all your
available rates, our API will filter by carrier options active". Una tarifa
cuyo ``code`` no tiene una opción ACTIVA con ese mismo ``code`` se descarta
sin aviso. Así que dar de alta el carrier no alcanza: hace falta una opción
por cada código que el callback puede contestar (``rates.quote``):

- cada ``option_code`` distinto de las tarifas activas de la tienda
  (``ShippingRate``), con el ``option_name`` de su primera fila;
- el de cada transportista que cotiza en el checkout y está activado
  (``apps.carriers.checkout.CARRIERS``, hoy ``"andreani"``), con el nombre
  configurado. Si la tabla ya usa ese código gana la tabla, igual que en
  ``shipping_rates.with_carrier_rates``.

Se sincronizan al dar de alta el carrier (``labels.register_carrier``) y, desde
ahí, cada vez que cambia la tabla o la configuración de un transportista
(``StoreProvider.checkout_prices_changed`` encola ``SYNC_EVENT``; el worker lo
corre en ``handlers.sync_carrier_options``). El resultado queda en
``preferences["shipping_carrier_options"]``.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from ...events import enqueue_event
from .. import get_provider

logger = logging.getLogger(__name__)

SYNC_EVENT = "internal/sync_carrier_options"
STATE_PREFERENCE = "shipping_carrier_options"


def carrier_id(connection):
    from .labels import CARRIER_ID_PREFERENCE

    return str((connection.preferences or {}).get(CARRIER_ID_PREFERENCE) or "")


def desired_options(connection):
    """``[(code, name), ...]``: una por cada código que el callback puede
    contestar hoy, en orden estable."""
    from apps.carriers.checkout import CARRIERS

    names = {}
    for code, name in connection.shipping_rates.filter(is_active=True).values_list("option_code", "option_name"):
        names.setdefault(code, name)
    for carrier in CARRIERS:
        settings_values = carrier.config(connection)
        if settings_values.get("enabled"):
            names.setdefault(carrier.OPTION_CODE, settings_values.get("name") or carrier.DEFAULT_NAME)
    return sorted(names.items())


def state(connection):
    """``status`` (``synced``/``failed``), ``codes``,
    ``created``, ``renamed``, ``inactive``, ``synced_at``, ``error``.
    ``{}`` = nunca se sincronizó."""
    return dict((connection.preferences or {}).get(STATE_PREFERENCE) or {})


def _save_state(connection, values):
    """Escribe con un UPDATE: el worker trae copias viejas de la conexión."""
    fresh = type(connection).objects.filter(pk=connection.pk).values_list("preferences", flat=True).first()
    preferences = dict(fresh or connection.preferences or {})
    preferences[STATE_PREFERENCE] = values
    connection.preferences = preferences
    type(connection).objects.filter(pk=connection.pk).update(preferences=preferences)


def sync(connection):
    """Crea o renombra las opciones del carrier. ``None`` si la tienda no
    tiene el carrier dado de alta; si no, el resumen guardado en el estado.
    Un error de la plataforma queda en el estado y se vuelve a lanzar."""
    current_carrier = carrier_id(connection)
    if not current_carrier:
        return None
    options = desired_options(connection)
    try:
        summary = get_provider(connection.platform).sync_shipping_carrier_options(
            connection, current_carrier, options
        )
    except Exception as exc:
        _save_state(connection, {**state(connection), "status": "failed", "error": str(exc)[:500]})
        raise
    result = {
        "status": "synced",
        "codes": [code for code, _ in options],
        **summary,
        "synced_at": timezone.now().isoformat(),
        "error": "",
    }
    _save_state(connection, result)
    if summary["inactive"]:
        # Las apagó el comerciante en su panel: es su decisión, no se
        # reactivan, pero esas tarifas no van a aparecer en el checkout.
        logger.info(
            "Tienda %s: opciones del carrier apagadas por el comerciante: %s.",
            connection.pk,
            ", ".join(summary["inactive"]),
        )
    return result


def enqueue_sync(connection):
    """Encola la sincronización si la tienda tiene el carrier dado de alta.
    Varios cambios seguidos encolan un solo evento (el pendiente se
    reutiliza) y el worker lee la tabla en el momento."""
    if not carrier_id(connection):
        return None
    event, _ = enqueue_event(platform=connection.platform, event_type=SYNC_EVENT, connection=connection)
    return event
