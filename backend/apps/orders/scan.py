"""Escanear para despachar (``escanear.html``): de lo que lee la cámara o la
pistola en el rótulo al pedido.

El rótulo trae dos códigos (``labels.label_rendering.build_label_context``):
el de barras es ``{{tracking}}`` — el número del transportista, o sin él el id
del pedido con ceros a la izquierda — y el QR es ``{{tracking_url}}`` — el
link del transportista, o nuestro link público firmado. Se acepta también el
número que ve el comprador, por si se tipea a mano.
"""

from urllib.parse import parse_qs, urlparse

from django.db.models import Q

from .public_tracking import order_id_from_token

MAX_MATCHES = 10
# El id con ceros a la izquierda del código de barras: f"{order.pk:06d}".
PADDED_ID_DIGITS = 6


def find_orders(queryset, code):
    """Los pedidos de ``queryset`` (ya recortado al comercio y a las tiendas
    de quien escanea) que corresponden a ``code``. Más de uno = el mismo
    número en dos tiendas: quien escanea elige."""
    code = (code or "").strip()
    if code.lower().startswith(("http://", "https://")):
        query = parse_qs(urlparse(code).query)
        token = (query.get("t") or [""])[0]
        if token:
            order_id = order_id_from_token(token)
            return list(queryset.filter(pk=order_id)) if order_id else []
        matches = list(queryset.filter(tracking_url=code)[:MAX_MATCHES])
        legacy_code = (query.get("codigo") or [""])[0]
        if matches or not legacy_code:
            return matches
        code = legacy_code

    code = code.lstrip("#").strip()
    if not code:
        return []
    by_tracking = list(queryset.filter(tracking_number=code)[:MAX_MATCHES])
    if by_tracking:
        return by_tracking
    lookup = Q(external_number=code)
    if code.isdigit() and len(code) >= PADDED_ID_DIGITS:
        lookup |= Q(pk=int(code))
    return list(queryset.filter(lookup)[:MAX_MATCHES])
