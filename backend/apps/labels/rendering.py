"""Render del rótulo en el servidor: datos + plantilla -> PDF.

Hasta ahora el PNG/PDF los armaba el navegador (``html2canvas``/``jsPDF``
sobre el editor, ver ``frontend/pedidos/diseñorotulos.html``): un rótulo
solo existía si había alguien mirando la pantalla. Este módulo mueve ese
render acá, sobre el mismo ``design`` que ya valida
``serializers.validate_design`` y define el editor — no lo reinventa, ver
el docstring de ``apps/labels/models.py``.

Funciones puras (reciben datos, devuelven bytes): nada de HTTP acá, para
que las pueda llamar tanto una vista (``views.py``) como, más adelante, un
proceso por lote.

Los códigos QR/de barras (``build_qr_drawing``/``build_barcode_drawing``)
salen enteramente de ``reportlab.graphics.barcode`` — no se agrega ``qrcode``
ni ``python-barcode``. Para el PDF alcanza con volcar el ``Drawing`` que
devuelven directo al canvas vectorial (``Drawing.drawOn``, sin pasar por
ningún backend de rasterizado). El endpoint de depuración/preview
(``GET /api/v1/labels/barcode/``, ver ``render_code_svg``) devuelve ese
mismo ``Drawing`` como SVG vía ``reportlab.graphics.renderSVG`` — Python
puro, viene con ReportLab. Deliberadamente NO se usa
``reportlab.graphics.renderPM``: en la versión de ReportLab que trae este
proyecto, ese módulo requiere el backend ``rlPyCairo`` (bindings de Cairo),
es decir la librería de sistema ``libcairo`` que justo se evitó al elegir
ReportLab por sobre WeasyPrint para el PDF.
"""

from __future__ import annotations

import re
from io import BytesIO

from django.conf import settings
from reportlab.graphics import renderSVG
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdf_canvas

# 1 cm = 72/2.54 puntos (unidad nativa de ReportLab).
CM_TO_POINTS = 72 / 2.54

FONT_NAME = "Helvetica"

# Marcador {{clave}} en el texto de un campo del diseño (ver punto 3 del
# prompt): permite que la MISMA plantilla sirva para envíos distintos.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# Caja cuadrada (en cm) reservada para el logo cuando el diseño no trae un
# tamaño propio (el editor tampoco lo guarda, solo left/top — ver
# KNOWN_DESIGN_FIELDS en serializers.py). Proporcional al lado más chico
# del rótulo para no desbordar rótulos pequeños.
def _box_size_cm(width_cm, height_cm):
    return max(1.0, min(float(width_cm), float(height_cm)) * 0.22)


# --- QR / código de barras --------------------------------------------------
#
# Tamaños en CENTÍMETROS (no en porcentaje del rótulo, a diferencia de
# left/top): un código escaneable necesita un tamaño físico mínimo y
# estirarlo junto con el rótulo lo vuelve ilegible. Los defaults reflejan
# el punto de integración con el courier (ver build_label_context): el QR
# apunta a la URL de seguimiento, el código de barras lleva el tracking.
DEFAULT_QR_SIZE_CM = 3.0
DEFAULT_QR_DATA_MARKER = "{{tracking_url}}"
DEFAULT_BARCODE_WIDTH_CM = 8.0
DEFAULT_BARCODE_HEIGHT_CM = 1.5
DEFAULT_BARCODE_SYMBOLOGY = "code128"
DEFAULT_BARCODE_DATA_MARKER = "{{tracking}}"

QR_SIZE_RANGE_CM = (1, 10)
BARCODE_WIDTH_RANGE_CM = (2, 20)
BARCODE_HEIGHT_RANGE_CM = (0.5, 5)

# Nombre de la clase de ReportLab (``getCodes()``) por cada simbología que
# acepta el diseño. Cualquier otro valor de ``symbology`` es un error.
BARCODE_SYMBOLOGY_CODES = {"code128": "Code128", "ean13": "EAN13"}

# Margen blanco extra alrededor de un código, además de la zona de
# silencio que YA trae cada widget de ReportLab por default (el QR
# reserva un borde de 4 módulos vía ``barBorder``; Code128/EAN13 reservan
# 10 módulos de ancho de barra a cada lado vía ``quiet``/``lquiet``/
# ``rquiet`` — ninguno de los dos se desactiva acá). Este margen es una
# segunda capa de seguridad para cuando el rótulo tenga otro fondo cerca.
QUIET_ZONE_CM = 0.2  # ~2 mm


def build_qr_drawing(data, size_cm):
    """``Drawing`` de ReportLab con un QR cuadrado de ``data``, de
    ``size_cm`` de lado. ``None`` si ``data`` está vacío: nunca se dibuja
    un código inválido, se deja el espacio en blanco."""
    if not data:
        return None
    size_pt = float(size_cm) * CM_TO_POINTS
    # isoScale=1 asegura que el QR nunca se deforme aunque en algún punto
    # se le pidan width/height distintos.
    return createBarcodeDrawing("QR", value=data, width=size_pt, height=size_pt, isoScale=1)


def build_barcode_drawing(data, symbology, width_cm, height_cm, show_text=True):
    """``Drawing`` de ReportLab con un código de barras 1D de ``width_cm``
    x ``height_cm``.

    Lanza ``ValueError`` (con un mensaje claro, para que la vista lo
    traduzca a un 400) si:

    - ``symbology`` no es una de las soportadas (``code128``/``ean13``).
    - ``symbology`` es ``ean13`` y ``data`` no son EXACTAMENTE 13 dígitos:
      ReportLab acepta cualquier largo sin quejarse (rellena/trunca por su
      cuenta) y dibuja igual un código roto que ningún lector reconoce —
      ese es justo el caso silencioso que hay que cortar acá.

    Devuelve ``None`` (no dibuja nada) si ``data`` queda vacío.
    """
    symbology = (symbology or DEFAULT_BARCODE_SYMBOLOGY).lower()
    code_name = BARCODE_SYMBOLOGY_CODES.get(symbology)
    if code_name is None:
        raise ValueError(
            "Simbología de código de barras desconocida: '%s' (válidas: %s)."
            % (symbology, ", ".join(sorted(BARCODE_SYMBOLOGY_CODES)))
        )
    if not data:
        return None
    if symbology == "ean13" and not re.fullmatch(r"\d{13}", data):
        raise ValueError(
            "EAN-13 requiere exactamente 13 dígitos numéricos (se recibió "
            "%r, %d caracter%s)." % (data, len(data), "es" if len(data) != 1 else "")
        )

    width_pt = float(width_cm) * CM_TO_POINTS
    height_pt = float(height_cm) * CM_TO_POINTS
    return createBarcodeDrawing(
        code_name, value=data, width=width_pt, height=height_pt, humanReadable=bool(show_text)
    )


def render_code_svg(drawing):
    """SVG (str) de un ``Drawing`` de ``build_qr_drawing``/
    ``build_barcode_drawing``, vía ``reportlab.graphics.renderSVG`` (Python
    puro, sin pasar por ``renderPM``/Cairo — ver el docstring del módulo)."""
    return renderSVG.drawToString(drawing)


def percent_to_canvas_xy(left_pct, top_pct, width_cm, height_cm):
    """Convierte una posición del editor (0-100, origen arriba-izquierda) a
    coordenadas de ReportLab (puntos, origen abajo-izquierda).

    Es una doble conversión y es el punto donde más fácil se cuela un
    error, así que queda aislada acá:

    1. porcentaje -> centímetros (sobre el ancho/alto real del rótulo).
    2. centímetros -> puntos (unidad nativa de ReportLab).
    3. el eje Y se invierte: en el editor ``top=0`` es el borde SUPERIOR,
       en un PDF el origen (y=0) es el borde INFERIOR.
    """
    x_cm = (float(left_pct) / 100) * float(width_cm)
    y_cm_from_top = (float(top_pct) / 100) * float(height_cm)
    y_cm_from_bottom = float(height_cm) - y_cm_from_top
    return x_cm * CM_TO_POINTS, y_cm_from_bottom * CM_TO_POINTS


def build_label_context(order=None, label=None):
    """Arma el diccionario de valores reales del envío, usado para
    reemplazar los marcadores ``{{clave}}`` del diseño (ver
    ``_replace_placeholders``).

    Sin ``order`` no hay de dónde sacar un destinatario/domicilio real, así
    que devuelve el contexto vacío (los rótulos existentes, con texto fijo
    en vez de marcadores, siguen mostrando ese texto tal cual). ``label``
    se acepta por si el llamador ya tiene la instancia a mano (p. ej. la
    vista del PDF de un rótulo propio); hoy no aporta nada que no venga de
    ``order``, queda reservado para cuando haga falta algo propio del
    rótulo (no del pedido).

    ``tracking``/``tracking_url`` son el punto de integración con el
    courier: mientras no exista esa API (ver ``apps.orders``), salen de un
    código derivado del id del pedido; el día que ``Order.tracking_number``/
    ``tracking_url`` se completen con el dato real del partner, el rótulo
    empieza a imprimir el código real sin tocar el render. Nunca quedan
    vacíos — un rótulo sin código escaneable no sirve.
    """
    if order is None:
        return {}

    recipient_name = order.user.get_full_name() or order.user.email
    tracking = order.tracking_number or f"BP-{order.pk:06d}"
    tracking_url = order.tracking_url or (
        f"{str(settings.FRONTEND_URL).rstrip('/')}/seguimiento.html?codigo={tracking}"
    )
    context = {
        "remitente": getattr(settings, "LABEL_SENDER_NAME", "") or "",
        "destinatario": recipient_name,
        "pedido": f"Pedido #{order.pk}",
        "tracking": tracking,
        "tracking_url": tracking_url,
    }

    address = getattr(order, "address", None)
    if address is not None:
        context["domicilio"] = " ".join(filter(None, [address.street, address.number]))
        context["cp"] = address.postal_code or ""
        context["localidad"] = ", ".join(filter(None, [address.city, address.state]))

    return context


def _replace_placeholders(text, context):
    """Reemplaza cada ``{{clave}}`` de ``text`` por su valor en ``context``.
    Un marcador desconocido o sin valor se reemplaza por cadena vacía —
    nunca deja un ``{{...}}`` a la vista en un rótulo impreso."""
    if not text:
        return ""

    def _sub(match):
        return str(context.get(match.group(1)) or "")

    return _PLACEHOLDER_RE.sub(_sub, text)


def _truncate_to_width(text, font_name, font_size, max_width_pt):
    """Corta ``text`` con elipsis si no entra en ``max_width_pt``. Nunca deja
    que el texto se derrame fuera del área imprimible del rótulo."""
    if max_width_pt <= 0:
        return ""
    if stringWidth(text, font_name, font_size) <= max_width_pt:
        return text
    ellipsis = "…"
    truncated = text
    while truncated and stringWidth(truncated + ellipsis, font_name, font_size) > max_width_pt:
        truncated = truncated[:-1]
    return (truncated + ellipsis) if truncated else ellipsis


def _draw_logo(pdf, entry, logo_file, width_cm, height_cm):
    """Dibuja el logo escalado dentro de una caja razonable, manteniendo la
    proporción. Si la imagen no se puede leer, no dibuja nada (nunca deja
    el placeholder "+ Logo" del editor en el PDF final)."""
    try:
        if hasattr(logo_file, "seek"):
            logo_file.seek(0)
        image = ImageReader(logo_file)
        img_w_px, img_h_px = image.getSize()
    except Exception:
        return
    if not img_w_px or not img_h_px:
        return

    box_cm = _box_size_cm(width_cm, height_cm)
    aspect = img_w_px / img_h_px
    if aspect >= 1:
        draw_w_cm, draw_h_cm = box_cm, box_cm / aspect
    else:
        draw_w_cm, draw_h_cm = box_cm * aspect, box_cm

    x_pt, y_top_pt = percent_to_canvas_xy(
        entry.get("left", 0), entry.get("top", 0), width_cm, height_cm
    )
    draw_w_pt, draw_h_pt = draw_w_cm * CM_TO_POINTS, draw_h_cm * CM_TO_POINTS
    pdf.drawImage(
        image,
        x_pt,
        y_top_pt - draw_h_pt,
        width=draw_w_pt,
        height=draw_h_pt,
        mask="auto",
        preserveAspectRatio=True,
    )


def _draw_white_box(pdf, x_pt, y_pt, w_pt, h_pt):
    """Fondo blanco sólido detrás de un código, aunque el rótulo tenga
    otro fondo (hoy el rótulo no dibuja ninguno, pero un código a medio
    tapar no se puede escanear)."""
    pdf.saveState()
    pdf.setFillColorRGB(1, 1, 1)
    pdf.rect(x_pt, y_pt, w_pt, h_pt, fill=1, stroke=0)
    pdf.restoreState()


def _fit_size_cm(size_cm, avail_w_cm, avail_h_cm):
    """Achica ``size_cm`` (ya sea un único lado o un ``(w, h)``) para que
    entre en el espacio disponible, PROPORCIONALMENTE — nunca estira ni
    deforma, solo reduce."""
    if isinstance(size_cm, tuple):
        w_cm, h_cm = size_cm
    else:
        w_cm = h_cm = size_cm
    scale = min(
        1.0,
        (avail_w_cm / w_cm) if w_cm else 1.0,
        (avail_h_cm / h_cm) if h_cm else 1.0,
    )
    scale = max(scale, 0.0)
    return (w_cm * scale, h_cm * scale)


def _draw_qr_field(pdf, entry, context, width_cm, height_cm):
    """Dibuja el QR real en la posición del campo ``qr`` del diseño (ver
    ``build_qr_drawing``). Formato: ``{"left", "top", "size" (cm,
    default 3), "data" (marcadores {{...}}, default {{tracking_url}})}``
    — compatible con un diseño viejo que solo tenga ``left``/``top``."""
    left_pct, top_pct = entry.get("left", 0), entry.get("top", 0)
    data = _replace_placeholders(entry.get("data", DEFAULT_QR_DATA_MARKER), context)
    if not data:
        return

    size_cm = float(entry.get("size", DEFAULT_QR_SIZE_CM))
    avail_w_cm = float(width_cm) * (1 - float(left_pct) / 100)
    avail_h_cm = float(height_cm) * (1 - float(top_pct) / 100)
    size_cm, _ = _fit_size_cm(size_cm, avail_w_cm, avail_h_cm)
    if size_cm <= 0:
        return

    drawing = build_qr_drawing(data, size_cm)
    if drawing is None:
        return

    x_pt, y_top_pt = percent_to_canvas_xy(left_pct, top_pct, width_cm, height_cm)
    size_pt = size_cm * CM_TO_POINTS
    margin_pt = QUIET_ZONE_CM * CM_TO_POINTS
    _draw_white_box(
        pdf, x_pt - margin_pt, y_top_pt - size_pt - margin_pt, size_pt + 2 * margin_pt, size_pt + 2 * margin_pt
    )
    drawing.drawOn(pdf, x_pt, y_top_pt - size_pt)


def _draw_barcode_field(pdf, entry, context, width_cm, height_cm):
    """Dibuja el código de barras 1D real en la posición del campo
    ``barcode`` del diseño (ver ``build_barcode_drawing``). Formato:
    ``{"left", "top", "width" (cm, default 8), "height" (cm, default 1.5),
    "symbology" (default "code128"), "data" (marcadores, default
    {{tracking}}), "show_text" (default True)}``."""
    left_pct, top_pct = entry.get("left", 0), entry.get("top", 0)
    data = _replace_placeholders(entry.get("data", DEFAULT_BARCODE_DATA_MARKER), context)
    symbology = entry.get("symbology", DEFAULT_BARCODE_SYMBOLOGY)
    show_text = entry.get("show_text", True)
    width_cm_req = float(entry.get("width", DEFAULT_BARCODE_WIDTH_CM))
    height_cm_req = float(entry.get("height", DEFAULT_BARCODE_HEIGHT_CM))

    avail_w_cm = float(width_cm) * (1 - float(left_pct) / 100)
    avail_h_cm = float(height_cm) * (1 - float(top_pct) / 100)
    bwidth_cm, bheight_cm = _fit_size_cm((width_cm_req, height_cm_req), avail_w_cm, avail_h_cm)
    if bwidth_cm <= 0 or bheight_cm <= 0:
        return

    # Puede lanzar ValueError (simbología desconocida / EAN-13 inválido):
    # se deja propagar, la vista lo traduce a un 400 con el mensaje tal cual.
    drawing = build_barcode_drawing(data, symbology, bwidth_cm, bheight_cm, show_text=show_text)
    if drawing is None:
        return

    x_pt, y_top_pt = percent_to_canvas_xy(left_pct, top_pct, width_cm, height_cm)
    w_pt, h_pt = bwidth_cm * CM_TO_POINTS, bheight_cm * CM_TO_POINTS
    margin_pt = QUIET_ZONE_CM * CM_TO_POINTS
    _draw_white_box(pdf, x_pt - margin_pt, y_top_pt - h_pt - margin_pt, w_pt + 2 * margin_pt, h_pt + 2 * margin_pt)
    drawing.drawOn(pdf, x_pt, y_top_pt - h_pt)


def draw_label_page(pdf, design, width_cm, height_cm, context=None, logo_file=None):
    """Dibuja ``design`` sobre la página ACTUAL de ``pdf`` (un
    ``reportlab.pdfgen.canvas.Canvas`` ya creado, con su tamaño de página
    ya seteado a ``width_cm``/``height_cm``). No crea el canvas ni llama
    ``showPage()``/``save()`` — eso lo decide el llamador, porque esta
    función es el único camino de dibujo tanto para un rótulo suelto
    (``render_label_pdf``, una página) como para el lote
    (``apps.labels.batch_views``, muchas páginas sobre el MISMO canvas).

    - Campos de texto: el ``text`` guardado, con sustitución de marcadores
      ``{{clave}}`` (ver ``_replace_placeholders``) y recorte con elipsis
      si no entra en el ancho disponible.
    - ``logo``: si viene ``logo_file``, se dibuja escalado manteniendo la
      proporción; si no, no se dibuja nada.
    - ``qr``/``barcode``: código real (ver ``_draw_qr_field``/
      ``_draw_barcode_field``); si ``data`` queda vacío tras resolver los
      marcadores, se deja el espacio en blanco. Puede lanzar ``ValueError``
      (simbología desconocida / EAN-13 inválido) — el llamador decide qué
      hacer con eso (ver el lote, que salta el ítem y sigue con el resto).
    """
    context = context or {}
    design = design or {}
    width_cm, height_cm = float(width_cm), float(height_cm)
    width_pt, height_pt = width_cm * CM_TO_POINTS, height_cm * CM_TO_POINTS

    # Tamaño de fuente proporcional a la altura del rótulo (built-in de
    # ReportLab, sin archivos externos).
    font_size = max(6.0, height_pt * 0.035)
    pdf.setFont(FONT_NAME, font_size)

    logo_entry = design.get("logo")
    if logo_entry and logo_file:
        _draw_logo(pdf, logo_entry, logo_file, width_cm, height_cm)

    qr_entry = design.get("qr")
    if qr_entry:
        _draw_qr_field(pdf, qr_entry, context, width_cm, height_cm)

    barcode_entry = design.get("barcode")
    if barcode_entry:
        _draw_barcode_field(pdf, barcode_entry, context, width_cm, height_cm)

    for key, entry in design.items():
        if key in ("logo", "qr", "barcode") or not isinstance(entry, dict):
            continue
        text = _replace_placeholders(entry.get("text"), context)
        if not text:
            continue
        x_pt, y_top_pt = percent_to_canvas_xy(
            entry.get("left", 0), entry.get("top", 0), width_cm, height_cm
        )
        max_width_pt = width_pt - x_pt
        text = _truncate_to_width(text, FONT_NAME, font_size, max_width_pt)
        if not text:
            continue
        # y_top_pt es el borde superior del campo; se baja una línea para
        # que el texto quede DEBAJO de esa línea, como en el editor.
        pdf.drawString(x_pt, y_top_pt - font_size, text)


def render_label_pdf(design, width_cm, height_cm, context=None, logo_file=None):
    """PDF de UN solo rótulo (una página, sin márgenes), en bytes. Wrapper
    de ``draw_label_page`` para el caso de un solo rótulo (endpoint
    individual: ``views.LabelViewSet.pdf`` / ``RenderLabelView``, y cada
    entrada de un ZIP en el lote)."""
    width_cm, height_cm = float(width_cm), float(height_cm)
    width_pt, height_pt = width_cm * CM_TO_POINTS, height_cm * CM_TO_POINTS

    buffer = BytesIO()
    pdf = pdf_canvas.Canvas(buffer, pagesize=(width_pt, height_pt))
    draw_label_page(pdf, design, width_cm, height_cm, context=context, logo_file=logo_file)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
