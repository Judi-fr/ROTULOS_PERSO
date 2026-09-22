"""Aviso de despacho a la tienda de origen (fase 4).

Cuando un pedido que vino de una tienda conectada pasa a un estado de envío
(``FULFILLMENT_STATUS_BY_ORDER_STATUS``) o, ya despachado, se le carga o
cambia el tracking, ``Order.save`` llama a ``notify_store_shipping_change``,
que solo encola ``internal/push_fulfillment``. El worker
(``handlers.push_fulfillment``) lee el pedido en ese momento y actualiza el
fulfillment order en la tienda: estado + número de seguimiento, con aviso al
comprador.
"""

from __future__ import annotations

import logging

from .events import enqueue_event
from .models import StoreConnection
from .stores import PUSH_FULFILLMENT_EVENT

logger = logging.getLogger(__name__)

# Estado local del pedido (apps.orders.Order.Status) -> estado del
# fulfillment order en Tiendanube.
FULFILLMENT_STATUS_BY_ORDER_STATUS = {
    "dispatched": "DISPATCHED",
    "in_transit": "DISPATCHED",
    "delivered": "DELIVERED",
}


def notify_store_shipping_change(order, *, status_changed, tracking_changed):
    """Encola el aviso a la tienda si corresponde. Devuelve el evento o
    ``None``. Nunca deja escapar una excepción: un aviso fallido no puede
    impedir guardar el pedido."""
    try:
        if getattr(order, "_skip_store_notification", False):
            # El cambio vino de la propia tienda (ver ingestion.upsert_store_order).
            return None
        if not order.store_connection_id or order.status not in FULFILLMENT_STATUS_BY_ORDER_STATUS:
            return None
        if not (status_changed or tracking_changed):
            return None
        connection = order.store_connection
        if connection.owner_id is None or connection.status == StoreConnection.Status.REVOKED:
            return None
        event, _ = enqueue_event(
            platform=connection.platform,
            event_type=PUSH_FULFILLMENT_EVENT,
            connection=connection,
            resource_id=order.external_id or "",
            # Solo el id: el worker lee el estado y el tracking actuales.
            payload={"order_id": order.pk},
        )
        return event
    except Exception:  # noqa: BLE001 - ver docstring
        logger.exception("No se pudo encolar el aviso de despacho del pedido %s", order.pk)
        return None
