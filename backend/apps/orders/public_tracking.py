"""Seguimiento público de un pedido: el link que lleva el QR del rótulo cuando
el pedido no tiene el seguimiento de un transportista, y la página
``seguimiento.html`` que lo abre sin sesión.

El link no lleva el id del pedido a la vista sino un valor firmado: con el id
solo (correlativo) cualquiera podría recorrer los estados de los pedidos de
todos los comercios. La página muestra el comercio, el número, el estado con
sus fechas, el transportista y la ciudad de destino — nunca quién recibe ni
la dirección, igual que el resto de lo que se ve desde afuera.
"""

from django.conf import settings
from django.core import signing

SALT = "orders.public-tracking"


def tracking_token(order):
    """El valor firmado que identifica al pedido en el link público."""
    return signing.dumps(order.pk, salt=SALT)


def order_id_from_token(token):
    """El id del pedido de un link público, o ``None`` si no es uno nuestro."""
    try:
        value = signing.loads(token or "", salt=SALT)
    except signing.BadSignature:
        return None
    return value if isinstance(value, int) else None


def public_tracking_url(order):
    """``<frontend>/seguimiento.html?t=<token>``: lo que va en el QR del rótulo."""
    return f"{str(settings.FRONTEND_URL).rstrip('/')}/seguimiento.html?t={tracking_token(order)}"
