"""Marca los pedidos cuyo rótulo ya salió (``Order.labels_printed_at``).

Se llama desde cada lugar que entrega rótulos para imprimir: el lote
(``labels.batch_views``), la impresión desde la tienda
(``integrations.store_print``) y el rótulo + etiqueta del transportista
(``carriers.printing.bundle``). Guarda solo la PRIMERA vez: reimprimir no la
mueve. Un ``update`` y no ``save()``: no es un cambio de estado, así que no
dispara la auditoría ni el aviso a la tienda de ``Order.save``.
"""

from django.utils import timezone

from .models import Order


def mark_labels_printed(order_ids):
    ids = {int(pk) for pk in order_ids if pk}
    if not ids:
        return 0
    return Order.objects.filter(pk__in=ids, labels_printed_at=None).update(labels_printed_at=timezone.now())
