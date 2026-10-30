"""Lo que se imprime al despachar con un transportista: por cada envío, NUESTRO
rótulo (la plantilla de la tienda, ya con el número del transportista en
``{{tracking}}``) seguido de la etiqueta del transportista, todo en un solo PDF
para mandar a la impresora de una vez.

Van los dos a propósito (decidido 2026-10-09 con Andreani, igual para todos):
el rótulo es el de la marca del cliente, y la etiqueta del transportista lleva
el código con el que él clasifica el paquete.
"""

from __future__ import annotations

import io
import logging

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from apps.integrations.store_print import resolve_template
from apps.labels.label_rendering import build_computed_context, build_label_context, render_label_pdf
from apps.labels.models import LabelTemplate

from .base import CarrierError
from .providers import get_provider

logger = logging.getLogger(__name__)


def _template_for(order):
    """La plantilla de la tienda del pedido (o la pública por defecto, como
    el lote de rótulos). ``None`` si no hay ninguna."""
    store = getattr(order, "store_connection", None)
    if store is not None:
        return resolve_template(store)
    return LabelTemplate.objects.filter(is_public=True, is_active=True).order_by("id").first()


def our_label(order):
    """El rótulo del pedido en PDF (bytes), o ``None`` si no hay plantilla o
    no se pudo dibujar: la etiqueta del transportista sale igual."""
    template = _template_for(order)
    if template is None:
        return None
    store = getattr(order, "store_connection", None)
    try:
        return render_label_pdf(
            template.design,
            template.width_cm,
            template.height_cm,
            context=build_label_context(order=order),
            logo_file=getattr(store, "logo", None) or None,
            computed=build_computed_context(owner=order.user),
        )
    except Exception:  # noqa: BLE001 - un diseño roto no frena el despacho
        logger.exception("No se pudo dibujar el rótulo del pedido %s.", order.pk)
        return None


def bundle(shipments):
    """Un PDF con, por cada envío, el rótulo y después la etiqueta de su
    transportista. ``CarrierError`` si alguno no da (o da ilegible) su
    etiqueta: imprimir medio lote confundiría más que avisar."""
    writer = PdfWriter()
    for shipment in shipments:
        own = our_label(shipment.order)
        if own:
            writer.append(PdfReader(io.BytesIO(own)))
        provider = get_provider(shipment.carrier)
        content = provider.label(shipment, "pdf")
        try:
            writer.append(PdfReader(io.BytesIO(content)))
        except (PdfReadError, ValueError) as exc:
            raise CarrierError(f"{provider.name} devolvió una etiqueta ilegible para el envío {shipment.tracking_number}.") from exc
    buffer = io.BytesIO()
    writer.write(buffer)
    from apps.orders.print_marks import mark_labels_printed

    mark_labels_printed(shipment.order_id for shipment in shipments)
    return buffer.getvalue()
