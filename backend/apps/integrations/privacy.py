"""Webhooks de privacidad obligatorios de Tiendanube (LGPD / datos personales).

- ``customers/redact``: borrar los datos personales de un comprador
  (``orders_to_redact``, más cualquier pedido de la tienda con su email).
- ``store/redact``: llega después de que el comerciante desinstala la app;
  borrar los datos de la tienda (se desconecta y se anonimizan todos sus
  pedidos y los payloads guardados de sus eventos).
- ``customers/data_request``: juntar los datos que guardamos de un comprador
  y enviárselos al comerciante (por email, con el reporte en JSON adjunto).

Los pedidos no se borran: se anonimizan (nombre, calle, número, CP,
referencia, email, teléfono y pedido crudo). Así el historial de envíos y
los contadores del comerciante siguen siendo coherentes, pero ya no hay
datos personales. Los handlers que llaman a esto están en ``handlers``.
"""

from __future__ import annotations

import json

from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.orders.models import Order

from .models import IntegrationEvent, StoreConnection
from .stores import disconnect_store

REDACTED = "[datos eliminados]"


def anonymize_order(order):
    address = order.address
    address.label = ""
    address.recipient_name = REDACTED
    address.street = REDACTED
    address.number = ""
    address.postal_code = ""
    address.reference = ""
    address.save(update_fields=["label", "recipient_name", "street", "number", "postal_code", "reference", "updated_at"])

    order.contact_email = ""
    order.contact_phone = ""
    order.raw_payload = {}
    order.save(update_fields=["contact_email", "contact_phone", "raw_payload", "updated_at"])


def customer_orders(connection, customer, order_ids):
    """Pedidos de la tienda que corresponden al comprador: los ids que manda
    Tiendanube y, por las dudas, cualquiera con su email."""
    ids = [str(order_id).strip() for order_id in (order_ids or []) if str(order_id).strip()]
    query = Q(external_id__in=ids)
    email = str((customer or {}).get("email") or "").strip()
    if email:
        query |= Q(contact_email__iexact=email)
    return Order.objects.filter(store_connection=connection).filter(query).select_related("address")


@transaction.atomic
def redact_customer(connection, payload):
    orders = list(customer_orders(connection, payload.get("customer"), payload.get("orders_to_redact")))
    for order in orders:
        anonymize_order(order)
    return len(orders)


@transaction.atomic
def redact_store(connection):
    if connection.status != StoreConnection.Status.REVOKED:
        disconnect_store(connection)

    orders = list(Order.objects.filter(store_connection=connection).select_related("address"))
    for order in orders:
        anonymize_order(order)

    connection.name = ""
    connection.store_url = ""
    connection.scopes = ""
    connection.last_error = ""
    connection.preferences = {}
    connection.save(update_fields=["name", "store_url", "scopes", "last_error", "preferences", "updated_at"])

    IntegrationEvent.objects.filter(connection=connection).update(payload={}, result={}, updated_at=timezone.now())
    return len(orders)


def _order_data(order):
    address = order.address
    return {
        "external_id": order.external_id,
        "external_number": order.external_number,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "status": order.status,
        "recipient_name": address.recipient_name,
        "street": address.street,
        "number": address.number,
        "city": address.city,
        "state": address.state,
        "postal_code": address.postal_code,
        "country": address.country,
        "reference": address.reference,
        "contact_email": order.contact_email,
        "contact_phone": order.contact_phone,
        "shipping_option": order.shipping_option,
        "items": order.items,
        "carrier": order.carrier,
        "tracking_number": order.tracking_number,
        "tracking_url": order.tracking_url,
    }


def build_data_report(connection, payload):
    customer = payload.get("customer") or {}
    orders = customer_orders(connection, customer, payload.get("orders_requested"))
    return {
        "generated_at": timezone.now().isoformat(),
        "data_request_id": (payload.get("data_request") or {}).get("id"),
        "store": {
            "platform": connection.platform,
            "external_store_id": connection.external_store_id,
            "name": connection.name,
        },
        "customer": {"id": customer.get("id"), "email": customer.get("email")},
        "orders": [_order_data(order) for order in orders],
        "notes": (
            "Datos del comprador que guarda la app para generar los rótulos de envío de sus pedidos. "
            "La app no guarda checkouts ni borradores de pedido."
        ),
    }


def send_data_report(connection, report):
    """Envía el reporte al dueño de la tienda. ``False`` si la tienda no
    tiene una cuenta con email vinculada (no hay a quién mandarlo)."""
    owner = connection.owner
    if owner is None or not owner.email:
        return False

    store_name = connection.name or f"tu tienda ({connection.external_store_id})"
    message = EmailMessage(
        subject=f"Pedido de datos personales de un comprador de {store_name}",
        body=(
            "Tiendanube nos pidió los datos personales que guardamos de uno de los compradores de "
            f"{store_name}.\n\n"
            "Te los enviamos adjuntos en formato JSON para que se los hagas llegar al comprador "
            "según lo que pide la ley de protección de datos personales.\n"
        ),
        to=[owner.email],
    )
    customer_id = report.get("customer", {}).get("id") or "sin-id"
    message.attach(
        f"datos-comprador-{customer_id}.json",
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        "application/json",
    )
    message.send(fail_silently=False)
    return True
