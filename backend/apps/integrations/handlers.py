"""Handlers de la cola de eventos de las tiendas conectadas (ver ``events``).

Se registran al importar este módulo, cosa que ``IntegrationsConfig.ready``
hace siempre: así el worker (``run_integrations_worker``) los encuentra.
Los genéricos se registran para CADA plataforma (``all_providers``), con los
nombres de evento que declara su proveedor; los de rótulos pedidos por la
tienda son solo de Tiendanube (Labels API). Todos son idempotentes (un
evento puede ejecutarse más de una vez):

- Avisos de pedido (``order_sync_events`` del proveedor): se pide el pedido
  completo a la API y se crea o actualiza. Entran todos los pedidos, no
  solo los pagados.
- Desinstalación (``uninstall_events``): desconecta la tienda.
- ``internal/store_setup`` (al conectar): registra los webhooks que falten
  y, si la tienda ya tiene dueño, lanza la importación inicial.
- ``internal/import_orders``: importa una página de pedidos y encola la
  siguiente, así un error a mitad de camino solo reintenta esa página.
- ``internal/reconcile_orders`` (plataformas con ``supports_reconciliation``,
  ver ``stores.enqueue_due_reconciliations``): reactiva los webhooks y trae
  los pedidos modificados desde el repaso anterior, por si algún aviso no
  llegó (en VTEX, lo que quedó en su feed de pedidos).
- ``internal/push_fulfillment`` (ver ``fulfillment``): informa a la tienda
  que el pedido se despachó/entregó, con su tracking.
- ``internal/push_shipping_rates`` (plataformas con ``supports_rates_push``,
  ver ``shipping_rates.rates_changed``): publica la tabla de tarifas.
- ``internal/generate_label`` + ``fulfillment_order/label_status_updated``:
  el rótulo que pidió el comerciante desde el admin de su propia tienda
  (solo Tiendanube: están en ``providers/tiendanube/handlers.py``, que se
  importa al final de este módulo para registrarlos).
- Privacidad (``privacy_events`` del proveedor: ``store/redact`` o
  ``shop/redact``, ``customers/redact``, ``customers/data_request``): ver
  ``privacy``. Funcionan aunque la tienda ya esté desconectada (el borrado
  de la tienda llega justamente después de desinstalar).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.audit.services import record
from apps.orders.ingestion import upsert_store_order
from apps.orders.models import Order

from . import privacy, shipping_rates
from .events import PermanentEventError, enqueue_event, register_handler
from .models import StoreConnection
from .providers import all_providers, get_provider
from .providers.base import ProviderAuthError, ProviderError, ProviderNotFoundError, ProviderRejectedError
from .stores import (
    IMPORT_ORDERS_EVENT,
    PUSH_FULFILLMENT_EVENT,
    PUSH_SHIPPING_RATES_EVENT,
    RECONCILE_ORDERS_EVENT,
    STORE_SETUP_EVENT,
    disconnect_store,
    enqueue_initial_import,
)

logger = logging.getLogger(__name__)

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
    _store_order(connection, provider, normalized)
    _mark_synced(connection)


def _store_order(connection, provider, normalized):
    """Crea o actualiza el pedido y, si la tienda recién ahora puede recibir
    el despacho que ya se hizo acá (VTEX: llegó la factura), se lo vuelve a
    informar. ``upsert_store_order`` no lo hace solo: un cambio que viene de
    la tienda nunca se le devuelve a ella."""
    order, _ = upsert_store_order(connection, normalized)
    if provider.needs_fulfillment_push(normalized, order):
        enqueue_event(
            platform=connection.platform,
            event_type=PUSH_FULFILLMENT_EVENT,
            connection=connection,
            resource_id=order.external_id or "",
            payload={"order_id": order.pk},
        )


def app_uninstalled(event):
    connection = event.connection
    if connection is not None and connection.status != StoreConnection.Status.REVOKED:
        disconnect_store(connection)


def _register_webhooks(connection, provider):
    """Registra (o reactiva) los webhooks de la tienda. Sin URL pública no se
    puede: queda el aviso en ``last_error``."""
    if not provider.webhook_events:
        # Plataforma sin avisos (Magento, fase 1): todo entra por el repaso,
        # así que tampoco hace falta la URL pública.
        return
    base_url = str(getattr(settings, "INTEGRATIONS_PUBLIC_BASE_URL", "") or "").rstrip("/")
    if base_url:
        webhook_url = f"{base_url}{reverse(f'{provider.platform}-webhooks')}"
        _call_api(
            connection, lambda: provider.register_webhooks(connection, webhook_url, provider.webhook_events)
        )
        provider.configure_admin_print(connection, base_url)
        if connection.last_error == MISSING_BASE_URL_ERROR:
            connection.last_error = ""
            connection.save(update_fields=["last_error", "updated_at"])
    else:
        logger.warning("Tienda %s conectada sin INTEGRATIONS_PUBLIC_BASE_URL: no se registran webhooks.", connection.pk)
        connection.last_error = MISSING_BASE_URL_ERROR
        connection.save(update_fields=["last_error", "updated_at"])


def setup_store(event):
    connection = _connection_for(event, require_owner=False)
    provider = get_provider(connection.platform)

    # Una tienda conectada con claves (WooCommerce) llega sin nombre: al
    # conectarla no se le pregunta nada (ver stores.connect_with_credentials).
    if not connection.name:
        try:
            info = provider.get_store_info(connection)
        except ProviderError as exc:
            logger.warning("No se pudo leer el nombre de la tienda %s: %s", connection.pk, exc)
        else:
            if info.name:
                connection.name = info.name[:150]
                connection.save(update_fields=["name", "updated_at"])

    _register_webhooks(connection, provider)

    if connection.owner_id is not None and provider.supports_order_import:
        enqueue_initial_import(connection)


def import_orders_page(event):
    connection = _connection_for(event, require_owner=True)
    provider = get_provider(connection.platform)
    # "page" es el formato de los eventos encolados antes de existir el cursor
    # (y el de la primera página, ver stores.enqueue_initial_import). La
    # página 1 es "sin cursor": un "1" no es un cursor válido en Shopify.
    cursor = str(event.payload.get("cursor") or "")
    legacy_page = int(event.payload.get("page") or 1)
    if not cursor and legacy_page > 1:
        cursor = str(legacy_page)
    since = str(event.payload.get("created_at_min") or "")
    page_size = provider.orders_page_size

    result = _call_api(
        connection,
        lambda: provider.list_orders_page(connection, created_at_min=since, cursor=cursor, per_page=page_size),
    )
    for raw in result.orders:
        try:
            normalized = provider.normalize_order(raw)
        except ValueError:
            logger.warning("Pedido sin id en la importación de la tienda %s (cursor %r).", connection.pk, cursor)
            continue
        upsert_store_order(connection, normalized)
    _mark_synced(connection)

    if result.next_cursor:
        enqueue_event(
            platform=connection.platform,
            event_type=IMPORT_ORDERS_EVENT,
            connection=connection,
            resource_id=result.next_cursor[:100],
            payload={"cursor": result.next_cursor, "created_at_min": since},
        )


def reconcile_orders(event):
    """Repaso de una tienda cuyos webhooks no alcanzan. La primera página
    además reactiva los webhooks (WooCommerce desactiva uno tras 5 entregas
    fallidas: sin esto, una caída nuestra de un rato la dejaría muda)."""
    connection = _connection_for(event, require_owner=True)
    provider = get_provider(connection.platform)
    cursor = str(event.payload.get("cursor") or "")
    updated_after = str(event.payload.get("updated_after") or "")
    if not updated_after:
        raise PermanentEventError("El repaso no dice desde cuándo.")

    if not cursor:
        _register_webhooks(connection, provider)

    result = _call_api(
        connection,
        lambda: provider.list_updated_orders_page(
            connection, updated_after=updated_after, cursor=cursor, per_page=provider.orders_page_size
        ),
    )
    for raw in result.orders:
        try:
            normalized = provider.normalize_order(raw)
        except ValueError:
            logger.warning("Pedido sin id en el repaso de la tienda %s.", connection.pk)
            continue
        _store_order(connection, provider, normalized)
    _mark_synced(connection)

    if result.next_cursor:
        enqueue_event(
            platform=connection.platform,
            event_type=RECONCILE_ORDERS_EVENT,
            connection=connection,
            resource_id=result.next_cursor[:100],
            payload=dict(event.payload, cursor=result.next_cursor),
        )


def push_fulfillment(event):
    connection = _connection_for(event, require_owner=True)
    order = Order.objects.filter(pk=event.payload.get("order_id"), store_connection=connection).first()
    if order is None or not order.external_id:
        raise PermanentEventError("El pedido ya no existe o no pertenece a esta tienda.")

    provider = get_provider(connection.platform)
    target_status = provider.fulfillment_status_for(order.status)
    if target_status is None:
        # Volvió a un estado previo al despacho antes de que el worker llegara:
        # no hay nada que informar.
        return

    _call_api(
        connection,
        lambda: provider.push_fulfillment(
            connection,
            order.external_id,
            status=target_status,
            tracking_code=order.tracking_number,
            tracking_url=order.tracking_url,
            carrier=order.carrier,
        ),
    )


def push_shipping_rates(event):
    """Publica la tabla de tarifas en una plataforma que cotiza con una tabla
    propia (VTEX). Lee la tabla en el momento: varios cambios seguidos se
    publican juntos. Despublicada (``enabled`` falso) manda una tabla vacía,
    que borra lo que habíamos subido."""
    connection = _connection_for(event, require_owner=True)
    provider = get_provider(connection.platform)
    enabled = bool(shipping_rates.rates_push_state(connection).get("enabled"))
    rates = list(connection.shipping_rates.filter(is_active=True).order_by("option_code", "pk")) if enabled else []
    try:
        summary = provider.push_shipping_rates(connection, rates)
    except (ProviderAuthError, ProviderNotFoundError, ProviderRejectedError) as exc:
        # Sin ``_call_api``: un permiso de logística que falta no es una
        # tienda caída (los pedidos siguen entrando), así que no la marca
        # "con errores". El motivo queda en el estado de la publicación.
        shipping_rates.save_rates_push_state(connection, status="failed", error=str(exc)[:500])
        raise PermanentEventError(str(exc)) from exc
    except Exception as exc:
        shipping_rates.save_rates_push_state(connection, status="failed", error=str(exc)[:500])
        raise
    shipping_rates.save_rates_push_state(
        connection,
        status="published",
        error="",
        published_at=timezone.now().isoformat(),
        rows=summary["rows"],
        unlinked_policies=summary["unlinked_policies"],
    )


# ---------------------------------------------------------------------------
# Privacidad (webhooks obligatorios de Tiendanube y de Shopify)
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


def store_redact(event):
    connection = event.connection
    if connection is None:
        return  # No guardamos nada de esa tienda.
    count = privacy.redact_store(connection)
    _privacy_audit("privacy.store_redact", connection, {"orders_redacted": {"from": None, "to": count}})


def customers_redact(event):
    payload = event.payload or {}
    order_ids = [str(order_id) for order_id in payload.get("orders_to_redact") or []]
    connection = event.connection
    count = privacy.redact_customer(connection, payload) if connection is not None else 0

    # El aviso trae email, teléfono y documento del comprador: tampoco se
    # guardan en la cola.
    event.payload = {
        "store_id": payload.get("store_id") or payload.get("shop_domain"),
        "orders_to_redact": order_ids,
    }
    event.save(update_fields=["payload", "updated_at"])

    if connection is not None:
        _privacy_audit("privacy.customer_redact", connection, {"orders_redacted": {"from": None, "to": count}})


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


# ---------------------------------------------------------------------------
# Registro por plataforma
# ---------------------------------------------------------------------------

PRIVACY_HANDLERS = {"store": store_redact, "customer": customers_redact, "data_request": customers_data_request}

for _provider in all_providers():
    for _event_type in _provider.order_sync_events:
        register_handler(_provider.platform, _event_type)(sync_order)
    for _event_type in _provider.uninstall_events:
        register_handler(_provider.platform, _event_type)(app_uninstalled)
    for _event_type, _kind in _provider.privacy_events.items():
        register_handler(_provider.platform, _event_type)(PRIVACY_HANDLERS[_kind])
    register_handler(_provider.platform, STORE_SETUP_EVENT)(setup_store)
    register_handler(_provider.platform, IMPORT_ORDERS_EVENT)(import_orders_page)
    register_handler(_provider.platform, PUSH_FULFILLMENT_EVENT)(push_fulfillment)
    if _provider.supports_reconciliation:
        register_handler(_provider.platform, RECONCILE_ORDERS_EVENT)(reconcile_orders)
    if _provider.supports_rates_push:
        register_handler(_provider.platform, PUSH_SHIPPING_RATES_EVENT)(push_shipping_rates)

# Los handlers propios de una plataforma viven en su carpeta; importarlos los
# registra.
