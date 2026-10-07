"""Despachar y cancelar UN pedido, con su auditoría.

Lo comparten el despacho de a uno (``OrderViewSet.ship``/``cancel``) y las
acciones masivas (``bulk_views``): un pedido despachado desde la carga de
seguimientos o desde el cambio de estado masivo sigue exactamente las mismas
reglas — estado solo hacia adelante, aviso a la tienda (lo hace
``Order.save``) y un ``order.ship``/``order.cancel`` con actor.
"""

from apps.audit.services import record

from .models import Order

TRACKED_SHIPPING_FIELDS = ("status", "carrier", "tracking_number", "tracking_url")


def apply_shipping(request, order, data):
    """Aplica ``data`` (ya validado por ``OrderShipSerializer``) y audita.

    Transportista y seguimiento cambian solo si vienen en ``data``. Devuelve
    True si algo cambió; si no cambió nada no guarda ni audita (y la tienda
    no recibe un aviso repetido)."""
    previous = {field: getattr(order, field) for field in TRACKED_SHIPPING_FIELDS}
    order.status = data["status"]
    for field in ("carrier", "tracking_number", "tracking_url"):
        if field in data:
            setattr(order, field, data[field].strip())
    changes = {
        field: {"from": previous[field], "to": getattr(order, field)}
        for field in TRACKED_SHIPPING_FIELDS
        if previous[field] != getattr(order, field)
    }
    if not changes:
        return False

    # La vista registra su propia auditoría con actor (order.ship): se evita
    # el order.status_change sin actor que dejaría Order.save.
    order._skip_status_audit = True
    order.save(update_fields=[*TRACKED_SHIPPING_FIELDS, "updated_at"])
    record(
        request,
        category="orders",
        action="order.ship",
        target=order,
        target_type="order",
        target_repr=str(order),
        changes=changes,
    )
    return True


def apply_cancel(request, order):
    """Cancela un pedido cancelable y audita. El llamador ya comprobó
    ``order.is_cancellable``."""
    previous_status = order.status
    # Mismo criterio que apply_shipping: esta cancelación SÍ tiene un actor
    # conocido (order.cancel), así que no se deja el rastro sin actor.
    order._skip_status_audit = True
    order.status = Order.Status.CANCELLED
    order.save(update_fields=["status", "updated_at"])
    record(
        request,
        category="orders",
        action="order.cancel",
        target=order,
        target_type="order",
        target_repr=str(order),
        changes={"status": {"from": previous_status, "to": order.status}},
    )
