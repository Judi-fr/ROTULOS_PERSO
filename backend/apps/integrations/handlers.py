"""Handlers de la cola de eventos para tiendas Tiendanube (ver ``events``).

Se registran al importar este módulo, cosa que ``IntegrationsConfig.ready``
hace siempre: así el worker (``run_integrations_worker``) los encuentra.
Todos son idempotentes (un evento puede ejecutarse más de una vez):

- Avisos de pedido (``ORDER_SYNC_EVENTS``): el webhook trae solo el id, así
  que se pide el pedido completo a la API y se crea o actualiza. Entran
  todos los pedidos, no solo los pagados.
- ``app/uninstalled``: desconecta la tienda.
- ``internal/store_setup`` (al conectar): registra los webhooks que falten
  y, si la tienda ya tiene dueño, lanza la importación inicial.
- ``internal/import_orders``: importa una página de pedidos y encola la
  siguiente, así un error a mitad de camino solo reintenta esa página.
- ``internal/push_fulfillment`` (ver ``fulfillment``): informa a la tienda
  que el pedido se despachó/entregó, con su tracking.
- ``internal/generate_label`` + ``fulfillment_order/label_status_updated``
  (ver ``store_labels``): el rótulo que pidió el comerciante desde el admin
  de su propia tienda.
- Privacidad (``store/redact``, ``customers/redact``,
  ``customers/data_request``): ver ``privacy``. Funcionan aunque la tienda
  ya esté desconectada (``store/redact`` llega justamente después de
  desinstalar).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.audit.services import record
from apps.orders.ingestion import upsert_store_order
from apps.orders.models import Order

from . import privacy, store_labels
from .events import PermanentEventError, enqueue_event, register_handler
from .fulfillment import FULFILLMENT_STATUS_BY_ORDER_STATUS
from .models import StoreConnection, StoreLabelRequest
from .providers import get_provider
from .providers.base import ProviderAuthError, ProviderNotFoundError, ProviderRejectedError
from .stores import (
    GENERATE_LABEL_EVENT,
    IMPORT_ORDERS_EVENT,
    PUSH_FULFILLMENT_EVENT,
    STORE_SETUP_EVENT,
    disconnect_store,
    enqueue_initial_import,
)

logger = logging.getLogger(__name__)

TIENDANUBE = StoreConnection.Platform.TIENDANUBE

ORDER_SYNC_EVENTS = (
    "order/created",
    "order/updated",
    "order/paid",
    "order/packed",
    "order/fulfilled",
    "order/cancelled",
    "order/edited",
    "order/pending",
    "order/voided",
)
LABEL_STATUS_EVENT = "fulfillment_order/label_status_updated"
WEBHOOK_EVENTS = ORDER_SYNC_EVENTS + ("app/uninstalled", LABEL_STATUS_EVENT)

# Estados de la plataforma en los que ya tiene el PDF guardado ella: a
# partir de ahí dejamos de publicarlo (ver store_labels.release_download).
LABEL_RELEASED_STATUSES = ("READY_TO_USE", "DOWNLOADED")

MISSING_BASE_URL_ERROR = (
    "No se registraron los webhooks: falta INTEGRATIONS_PUBLIC_BASE_URL (la URL pública HTTPS del backend)."
)


def _connection_for(event, *, require_owner):
    connection = event.connection
    if connection is None:
        raise PermanentEventError("El aviso no corresponde a ninguna tienda conectada.")
    if connection.status == StoreConnection.Status.REVOKED:
        raise PermanentEventError("La tienda está desconectada.")
    if require_owner and connection.owner_id is None:
        raise PermanentEventError("La tienda todavía no está vinculada a una cuenta.")
    return connection


def _call_api(connection, func):
    """Ejecuta una llamada a la plataforma traduciendo los errores que no se
    arreglan reintentando. Un token rechazado deja la tienda en ``error``
    (se ve en la lista de tiendas; reinstalar la app lo resuelve)."""
    try:
        return func()
    except ProviderAuthError as exc:
        connection.status = StoreConnection.Status.ERROR
        connection.last_error = str(exc)[:2000]
        connection.save(update_fields=["status", "last_error", "updated_at"])
        raise PermanentEventError(str(exc)) from exc
    except (ProviderNotFoundError, ProviderRejectedError) as exc:
        raise PermanentEventError(str(exc)) from exc


def _mark_synced(connection):
    connection.last_synced_at = timezone.now()
    connection.save(update_fields=["last_synced_at", "updated_at"])


def sync_order(event):
    connection = _connection_for(event, require_owner=True)
    provider = get_provider(connection.platform)
    order_id = event.resource_id or str(event.payload.get("id") or "")
    if not order_id:
        raise PermanentEventError("El aviso no trae el id del pedido.")

    raw = _call_api(connection, lambda: provider.get_order(connection, order_id))
    try:
        normalized = provider.normalize_order(raw)
    except ValueError as exc:
        raise PermanentEventError(str(exc)) from exc
    upsert_store_order(connection, normalized)
    _mark_synced(connection)


for _event_type in ORDER_SYNC_EVENTS:
    register_handler(TIENDANUBE, _event_type)(sync_order)


@register_handler(TIENDANUBE, "app/uninstalled")
def app_uninstalled(event):
    connection = event.connection
    if connection is not None and connection.status != StoreConnection.Status.REVOKED:
        disconnect_store(connection)


@register_handler(TIENDANUBE, STORE_SETUP_EVENT)
def setup_store(event):
    connection = _connection_for(event, require_owner=False)
    provider = get_provider(connection.platform)

    base_url = str(getattr(settings, "INTEGRATIONS_PUBLIC_BASE_URL", "") or "").rstrip("/")
    if base_url:
        webhook_url = f"{base_url}{reverse('tiendanube-webhooks')}"
        _call_api(connection, lambda: provider.register_webhooks(connection, webhook_url, WEBHOOK_EVENTS))
        if connection.last_error == MISSING_BASE_URL_ERROR:
            connection.last_error = ""
            connection.save(update_fields=["last_error", "updated_at"])
    else:
        logger.warning("Tienda %s conectada sin INTEGRATIONS_PUBLIC_BASE_URL: no se registran webhooks.", connection.pk)
        connection.last_error = MISSING_BASE_URL_ERROR
        connection.save(update_fields=["last_error", "updated_at"])

    if connection.owner_id is not None:
        enqueue_initial_import(connection)


@register_handler(TIENDANUBE, IMPORT_ORDERS_EVENT)
def import_orders_page(event):
    connection = _connection_for(event, require_owner=True)
    provider = get_provider(connection.platform)
    page = max(int(event.payload.get("page") or 1), 1)
    since = str(event.payload.get("created_at_min") or "")
    page_size = getattr(settings, "TIENDANUBE_ORDERS_PAGE_SIZE", 200)

    raws = _call_api(
        connection,
        lambda: provider.list_orders(connection, created_at_min=since, page=page, per_page=page_size),
    )
    for raw in raws:
        try:
            normalized = provider.normalize_order(raw)
        except ValueError:
            logger.warning("Pedido sin id en la importación de la tienda %s (página %s).", connection.pk, page)
            continue
        upsert_store_order(connection, normalized)
    _mark_synced(connection)

    if len(raws) >= page_size:
        enqueue_event(
            platform=connection.platform,
            event_type=IMPORT_ORDERS_EVENT,
            connection=connection,
            resource_id=str(page + 1),
            payload={"page": page + 1, "created_at_min": since},
        )


@register_handler(TIENDANUBE, PUSH_FULFILLMENT_EVENT)
def push_fulfillment(event):
    connection = _connection_for(event, require_owner=True)
    order = Order.objects.filter(pk=event.payload.get("order_id"), store_connection=connection).first()
    if order is None or not order.external_id:
        raise PermanentEventError("El pedido ya no existe o no pertenece a esta tienda.")

    target_status = FULFILLMENT_STATUS_BY_ORDER_STATUS.get(order.status)
    if target_status is None:
        # Volvió a un estado previo al despacho antes de que el worker llegara:
        # no hay nada que informar.
        return

    provider = get_provider(connection.platform)
    _call_api(
        connection,
        lambda: provider.push_fulfillment(
            connection,
            order.external_id,
            status=target_status,
            tracking_code=order.tracking_number,
            tracking_url=order.tracking_url,
        ),
    )


# ---------------------------------------------------------------------------
# Rótulos pedidos desde el admin de la tienda (ver store_labels)
# ---------------------------------------------------------------------------


@register_handler(TIENDANUBE, GENERATE_LABEL_EVENT)
def generate_store_label(event):
    """Dibuja el rótulo que pidió la tienda y le avisa dónde bajarlo.

    Un rótulo que no se puede dibujar ya quedó informado como fallido del
    lado de la plataforma (``store_labels.generate``), así que acá solo se
    traduce a un error que la cola no reintenta.
    """
    connection = _connection_for(event, require_owner=False)
    label_request = StoreLabelRequest.objects.filter(
        pk=event.payload.get("label_request_id"), connection=connection
    ).first()
    if label_request is None:
        raise PermanentEventError("El rótulo pedido ya no existe o no es de esta tienda.")

    try:
        store_labels.generate(label_request)
    except store_labels.LabelGenerationError as exc:
        raise PermanentEventError(str(exc)) from exc
    except (ProviderAuthError, ProviderNotFoundError, ProviderRejectedError) as exc:
        # La plataforma rechazó el aviso (etiqueta vencida, cancelada o un
        # token que ya no sirve): reintentar no lo arregla.
        store_labels.fail(label_request, str(exc))
        raise PermanentEventError(str(exc)) from exc


@register_handler(TIENDANUBE, LABEL_STATUS_EVENT)
def label_status_updated(event):
    """La plataforma avisa en qué quedó una etiqueta. Solo nos importa para
    dejar de publicar el PDF: una vez que lo tiene ella, nuestra URL
    pública no tiene por qué seguir existiendo."""
    connection = event.connection
    if connection is None:
        return  # No es una tienda nuestra: no hay nada que liberar.

    payload = event.payload or {}
    label_id = str(payload.get("label_id") or payload.get("id") or event.resource_id or "").strip()
    if not label_id:
        return

    label_request = StoreLabelRequest.objects.filter(
        connection=connection, external_label_id=label_id
    ).first()
    if label_request is None:
        return

    # Solo con un estado conocido: sin estado no se sabe si la plataforma
    # alcanzó a bajar el PDF, y dejar de publicarlo antes de tiempo le
    # rompería la etiqueta al comerciante.
    status = str(payload.get("status") or "").strip().upper()
    if status in LABEL_RELEASED_STATUSES:
        store_labels.release_download(label_request)


# ---------------------------------------------------------------------------
# Privacidad (webhooks obligatorios de Tiendanube)
# ---------------------------------------------------------------------------


def _privacy_audit(action, connection, changes):
    record(
        None,
        category="integrations",
        action=action,
        target=connection,
        target_type="storeconnection",
        # Nunca el nombre ni datos del comprador: solo la tienda y conteos.
        target_repr=f"{connection.get_platform_display()} {connection.external_store_id}",
        changes=changes,
    )


@register_handler(TIENDANUBE, "store/redact")
def store_redact(event):
    connection = event.connection
    if connection is None:
        return  # No guardamos nada de esa tienda.
    count = privacy.redact_store(connection)
    _privacy_audit("privacy.store_redact", connection, {"orders_redacted": {"from": None, "to": count}})


@register_handler(TIENDANUBE, "customers/redact")
def customers_redact(event):
    payload = event.payload or {}
    order_ids = [str(order_id) for order_id in payload.get("orders_to_redact") or []]
    connection = event.connection
    count = privacy.redact_customer(connection, payload) if connection is not None else 0

    # El aviso trae email, teléfono y documento del comprador: tampoco se
    # guardan en la cola.
    event.payload = {"store_id": payload.get("store_id"), "orders_to_redact": order_ids}
    event.save(update_fields=["payload", "updated_at"])

    if connection is not None:
        _privacy_audit("privacy.customer_redact", connection, {"orders_redacted": {"from": None, "to": count}})


@register_handler(TIENDANUBE, "customers/data_request")
def customers_data_request(event):
    connection = event.connection
    if connection is None:
        raise PermanentEventError("El pedido de datos no corresponde a ninguna tienda que tengamos.")

    report = privacy.build_data_report(connection, event.payload or {})
    event.result = report
    event.save(update_fields=["result", "updated_at"])

    if not privacy.send_data_report(connection, report):
        raise PermanentEventError(
            "La tienda no está vinculada a una cuenta con email: el reporte quedó guardado en este evento "
            "(campo result) para enviárselo al comerciante a mano."
        )
    _privacy_audit(
        "privacy.data_request",
        connection,
        {"orders_reported": {"from": None, "to": len(report["orders"])}, "sent_to_owner": {"from": None, "to": True}},
    )
