"""Planilla de retiro: el PDF que firma el transportista al llevarse los
paquetes (``bulk_views.DispatchManifestView``).

Es la constancia de entrega del comerciante al transportista: qué pedidos,
cuántos bultos y cuánto peso salieron, con firma, aclaración y hora. A
diferencia del rótulo, no va pegada en el paquete, pero igual lleva solo lo
necesario para identificar cada envío: nada de productos, precios ni datos
de contacto del comprador.

Se arma con platypus (reportlab) y no con el canvas a mano de los rótulos:
acá sí hace falta una tabla que se parta en páginas y repita el encabezado.
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .bulk import format_weight, order_number

_BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=8.5, leading=10.5)
_BODY_BOLD = ParagraphStyle("body_bold", parent=_BODY, fontName="Helvetica-Bold")
_HEAD = ParagraphStyle("head", parent=_BODY, fontName="Helvetica-Bold", textColor=colors.white)
_TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=16, leading=20)
_INFO = ParagraphStyle("info", fontName="Helvetica", fontSize=9.5, leading=13)


def _p(text, style=_BODY):
    # Todo lo que viene de una tienda es texto de terceros: Paragraph
    # interpreta marcado, así que se escapa siempre.
    return Paragraph(escape(str(text or "")), style)


def manifest_sender(orders, user):
    """Quién entrega: el remitente de la tienda si todos los pedidos son de
    la misma; si no (o son cargados a mano), el comerciante dueño de la
    cuenta. Mismo criterio de remitente que el rótulo
    (``label_rendering.build_label_context``)."""
    stores = {order.store_connection_id for order in orders}
    store = orders[0].store_connection if len(stores) == 1 and orders else None
    name = (
        ((store.sender_name or "").strip() or (store.name or "").strip()) if store else ""
    ) or user.get_full_name() or user.email
    address = (store.sender_address or "").strip() if store else ""
    phone = (store.sender_phone or "").strip() if store else ""
    return name, address, phone


def _weight_text(value):
    return "" if value is None else f"{format_weight(value)} kg"


def render_manifest_pdf(orders, user, carrier=""):
    """``orders``: lista de ``Order`` (con address y store_connection
    cargados), en el orden en que se imprimen."""
    buffer = BytesIO()
    now = timezone.localtime()
    sender_name, sender_address, sender_phone = manifest_sender(orders, user)

    def draw_footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(1.5 * cm, 1 * cm, f"Planilla de retiro · {now:%d/%m/%Y %H:%M}")
        canvas.drawRightString(A4[0] - 1.5 * cm, 1 * cm, f"Página {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.8 * cm,
        title="Planilla de retiro",
    )

    total_packages = sum(order.package_count or 0 for order in orders)
    weights = [order.total_weight_kg for order in orders if order.total_weight_kg is not None]
    total_weight = sum(weights, Decimal("0")) if weights else None

    story = [Paragraph("Planilla de retiro", _TITLE), Spacer(1, 6)]
    info_lines = [f"<b>Remitente:</b> {escape(sender_name)}"]
    if sender_address:
        info_lines.append(f"<b>Domicilio:</b> {escape(sender_address)}")
    if sender_phone:
        info_lines.append(f"<b>Teléfono:</b> {escape(sender_phone)}")
    info_lines.append(f"<b>Transportista:</b> {escape(carrier) if carrier else '______________________________'}")
    info_lines.append(f"<b>Fecha:</b> {now:%d/%m/%Y}")
    summary = f"<b>Pedidos:</b> {len(orders)} &nbsp;&nbsp; <b>Bultos:</b> {total_packages}"
    if total_weight is not None:
        summary += f" &nbsp;&nbsp; <b>Peso total:</b> {escape(_weight_text(total_weight))}"
        if len(weights) < len(orders):
            summary += " (algunos pedidos no tienen peso)"
    info_lines.append(summary)
    for line in info_lines:
        story.append(Paragraph(line, _INFO))
    story.append(Spacer(1, 10))

    header = [_p(text, _HEAD) for text in ("#", "Pedido", "Destinatario", "Domicilio", "Localidad / CP", "Bultos", "Peso", "Seguimiento")]
    rows = [header]
    for index, order in enumerate(orders, start=1):
        address = order.address
        street = " ".join(filter(None, [address.street, address.number]))
        locality = ", ".join(filter(None, [address.city, address.state]))
        if address.postal_code:
            locality = f"{locality} ({address.postal_code})" if locality else address.postal_code
        recipient = address.recipient_name or order.user.get_full_name() or order.user.email
        store_name = order.store_connection.name if order.store_connection else ""
        number = f"#{order_number(order)}"
        rows.append(
            [
                _p(index),
                [_p(number, _BODY_BOLD)] + ([_p(store_name)] if store_name else []),
                _p(recipient),
                _p(street),
                _p(locality),
                _p(order.package_count),
                _p(_weight_text(order.total_weight_kg)),
                _p(order.tracking_number),
            ]
        )

    # Suman 18 cm (A4 menos márgenes); "Bultos", "Seguimiento" y un
    # número de fila de tres cifras no se parten.
    widths = [0.9, 2.3, 3.0, 3.3, 2.9, 1.5, 1.3, 2.8]
    table = Table(rows, colWidths=[w * cm for w in widths], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9ca3af")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 28))

    # El bloque de firmas no se parte entre dos páginas: una firma sola en
    # una hoja aparte no prueba qué se retiró.
    line = "_" * 34
    signatures = Table(
        [
            [_p(line), _p(line)],
            [_p("Firma del transportista"), _p("Aclaración")],
            [Spacer(1, 18), Spacer(1, 18)],
            [_p(line), _p(line)],
            [_p("DNI"), _p("Fecha y hora de retiro")],
        ],
        colWidths=[8.5 * cm, 8.5 * cm],
    )
    story.append(
        KeepTogether(
            [
                Paragraph(
                    f"Recibí {len(orders)} pedido(s) / {total_packages} bulto(s) en conformidad.",
                    _INFO,
                ),
                Spacer(1, 22),
                signatures,
            ]
        )
    )

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buffer.getvalue()
