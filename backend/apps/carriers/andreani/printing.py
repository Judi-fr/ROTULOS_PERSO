"""Lo que se imprime al despachar con Andreani: por cada envío, NUESTRO rótulo
(la plantilla de la tienda, ya con el número de Andreani en ``{{tracking}}``)
seguido de la etiqueta de Andreani, todo en un solo PDF para mandar a la
impresora de una vez.

Van los dos a propósito (decidido 2026-10-09): el rótulo es el de la marca del
cliente, y la etiqueta de Andreani lleva el código de barras con el que
Andreani clasifica el paquete. A CONFIRMAR con un ejecutivo de Andreani si
alcanza con uno solo.
"""

from __future__ import annotations

import io
import logging

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from ..printing import our_label
from .client import AndreaniClient, AndreaniError

logger = logging.getLogger(__name__)


def bundle(shipments):
    """Un PDF con, por cada envío, el rótulo y después la etiqueta de
    Andreani. ``AndreaniError`` si Andreani no da (o da ilegible) alguna
    etiqueta: imprimir medio lote confundiría más que avisar."""
    writer = PdfWriter()
    for shipment in shipments:
        own = our_label(shipment.order)
        if own:
            writer.append(PdfReader(io.BytesIO(own)))
        content = AndreaniClient(shipment.account).label(shipment.group_number or shipment.tracking_number, fmt="pdf")
        try:
            writer.append(PdfReader(io.BytesIO(content)))
        except (PdfReadError, ValueError) as exc:
            raise AndreaniError(f"Andreani devolvió una etiqueta ilegible para el envío {shipment.tracking_number}.") from exc
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()
