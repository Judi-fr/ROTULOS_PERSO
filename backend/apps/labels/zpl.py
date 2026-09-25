"""Salida ZPL para impresoras térmicas Zebra (Sistema 1).

El mismo ``design`` que dibuja ``label_rendering.draw_label_page`` en un PDF,
emitido como el lenguaje nativo de una Zebra. **No es un formato de salida
más: es otro camino de dibujo**, y por eso vive en su propio módulo en vez de
como una rama dentro del renderer de PDF.

Por qué existe: una térmica imprimiendo un PDF depende del driver, rasteriza
la página entera y sale lenta y borrosa. En ZPL el texto y los códigos son
comandos nativos — el QR y el código de barras los dibuja el firmware de la
impresora, con sus propios módulos perfectamente cuadrados —, así que sale
nítido y en una fracción del tiempo. **Pero el PDF sigue siendo el camino que
funciona siempre**: si una impresora no está contemplada acá, el comerciante
imprime el PDF y no se queda sin despachar.

Tres diferencias con el PDF que conviene tener presentes:

- **El origen es arriba a la izquierda**, igual que el ``left``/``top`` del
  editor. No hay que invertir el eje Y como en ReportLab
  (``percent_to_canvas_xy``): acá la conversión es directa.
- **Todo se mide en dots**, no en puntos ni centímetros, y cuántos dots entran
  en un centímetro depende del modelo: 8 dots/mm en una de 203 dpi (la
  mayoría), 12 en una de 300. Por eso ``dpmm`` es un parámetro y no una
  constante. No se usa ``^MU`` (el comando de Zebra para reescalar un formato
  escrito para otra resolución) porque acá el ZPL se genera en cada pedido:
  se calculan los dots que correspondan y listo. ``^MU`` es para plantillas
  fijas que no se pueden regenerar.
- **El ancho del texto lo resuelve la impresora**, con ``^FB``, en vez de
  medirse acá: las métricas de la fuente 0 de Zebra no son las de Helvetica,
  así que cualquier cuenta nuestra sería una aproximación. Se le da la caja y
  la alineación, y el firmware recorta.
"""

from __future__ import annotations

import logging

from .label_rendering import (
    BORDER_INSET_CM,
    DECORATION_KEYS,
    DEFAULT_BARCODE_DATA_MARKER,
    DEFAULT_BARCODE_HEIGHT_CM,
    DEFAULT_BARCODE_SYMBOLOGY,
    DEFAULT_BARCODE_WIDTH_CM,
    DEFAULT_QR_DATA_MARKER,
    DEFAULT_QR_SIZE_CM,
    MIN_SHRINK_FONT_SIZE,
    _all_placeholders_empty,
    _box_size_cm,
    _fit_size_cm,
    _replace_placeholders,
    apply_design_rules,
)

logger = logging.getLogger(__name__)

# Densidades de impresión que existen en el parque Zebra, en dots por
# milímetro. 8 dpmm (203 dpi) es lo que traen ZD220/ZD230/GK420 y compañía,
# que es la enorme mayoría de lo que se usa para despachar; 12 dpmm (300 dpi)
# aparece en etiquetas chicas con mucho texto.
DPMM_203 = 8
DPMM_300 = 12
SUPPORTED_DPMM = (6, 8, 12, 24)
DEFAULT_DPMM = DPMM_203

# Puntos PostScript por pulgada: el diseño guarda los tamaños de letra en
# puntos (es lo que entiende ReportLab) y acá hay que pasarlos a dots.
POINTS_PER_INCH = 72.0
MM_PER_INCH = 25.4

# Grosor de trazo del recuadro y las líneas. El PDF usa setLineWidth(0.6);
# acá se convierte a dots y nunca baja de 1, porque un trazo de 0 dots no
# se imprime.
DECORATION_LINE_WIDTH_PT = 0.6

# Largo del guión y del hueco de una línea punteada, en mm. ZPL no tiene
# trazo punteado: un ^GB es siempre sólido, así que se dibuja a pedacitos.
DASH_MM = 2.0

# Corrección de error del QR. M (~15%) es el punto medio habitual para una
# etiqueta de envío: aguanta un roce o una gota sin agrandar el código como
# lo haría Q o H.
QR_ERROR_CORRECTION = "M"

# Rango que acepta ZPL para la magnificación del QR y para el ancho de
# módulo del código de barras (^BY).
QR_MAGNIFICATION_RANGE = (1, 10)
BARCODE_MODULE_RANGE = (1, 10)

ALIGN_TO_ZPL = {"left": "L", "center": "C", "right": "R"}


# ---------------------------------------------------------------------------
# Unidades
# ---------------------------------------------------------------------------


def cm_to_dots(value_cm, dpmm):
    """Centímetros a dots, redondeado al entero más cercano: ZPL no acepta
    coordenadas fraccionarias."""
    return int(round(float(value_cm) * 10 * dpmm))


def points_to_dots(value_pt, dpmm):
    """Puntos PostScript a dots. Es la conversión que mantiene el mismo
    tamaño de letra que el PDF."""
    return int(round(float(value_pt) / POINTS_PER_INCH * MM_PER_INCH * dpmm))


def percent_to_dots(left_pct, top_pct, width_cm, height_cm, dpmm):
    """Posición del editor (0-100, origen arriba-izquierda) a dots.

    A diferencia de ``label_rendering.percent_to_canvas_xy``, acá NO se
    invierte el eje Y: el origen de ZPL ya es la esquina superior izquierda,
    igual que el del editor.
    """
    x_cm = (float(left_pct) / 100) * float(width_cm)
    y_cm = (float(top_pct) / 100) * float(height_cm)
    return cm_to_dots(x_cm, dpmm), cm_to_dots(y_cm, dpmm)


def _clamp(value, low, high):
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------


def escape_field_data(text):
    """Texto listo para ir dentro de un ``^FD``.

    ``^`` y ``~`` son los prefijos de comando de ZPL y ``\\`` es el escape de
    ``^FH``: si alguno aparece tal cual en el dato, la impresora deja de leer
    texto y empieza a interpretar comandos. Una URL de seguimiento con un
    ``~`` alcanza para romper la etiqueta entera, así que los tres se mandan
    en hexadecimal (el llamador emite ``^FH\\`` antes del ``^FD``).
    """
    return (
        str(text)
        .replace("\\", "\\5C")
        .replace("^", "\\5E")
        .replace("~", "\\7E")
    )


def _text_commands(entry, context, computed, width_cm, height_cm, default_font_pt, dpmm):
    """Los comandos de un campo de texto, o ``[]`` si no hay nada que
    imprimir. Mismas reglas que ``label_rendering._draw_text_entry``."""
    raw_text = entry.get("text")
    if entry.get("hide_if_empty") and _all_placeholders_empty(raw_text, context, computed):
        return []
    text = _replace_placeholders(raw_text, context, computed)
    if not text:
        return []

    left_pct, top_pct = entry.get("left", 0), entry.get("top", 0)
    x_dots, y_dots = percent_to_dots(left_pct, top_pct, width_cm, height_cm, dpmm)

    # Ancho de la caja: hasta el borde derecho salvo que el campo diga otra
    # cosa, igual que en el PDF.
    total_width_dots = cm_to_dots(width_cm, dpmm)
    box_width_dots = total_width_dots - x_dots
    if entry.get("width") is not None:
        box_width_dots = min(box_width_dots, cm_to_dots(float(entry["width"]) / 100 * width_cm, dpmm))
    if box_width_dots <= 0:
        return []

    font_pt = float(entry.get("font_size") or default_font_pt)
    if entry.get("shrink_to_fit"):
        font_pt = _shrink_to_fit_pt(text, font_pt, box_width_dots, dpmm)
    font_dots = max(1, points_to_dots(font_pt, dpmm))

    align = ALIGN_TO_ZPL.get(entry.get("align", "left"), "L")
    payload = escape_field_data(text)

    def campo(offset):
        # ^A0N: fuente 0 (la escalable que traen todas), sin rotar, alto en
        # dots y ancho 0 = proporcional al alto.
        # ^FB: caja de <ancho>, UNA línea. El "1" es lo que hace que el
        # firmware recorte lo que no entra en vez de seguir escribiendo fuera
        # del rótulo; el PDF hace lo mismo con una elipsis
        # (_truncate_to_width).
        return [
            f"^FO{x_dots + offset},{y_dots + offset}",
            f"^A0N,{font_dots},0",
            f"^FB{box_width_dots},1,0,{align},0",
            "^FH\\",
            f"^FD{payload}^FS",
        ]

    commands = campo(0)
    if entry.get("bold"):
        # ZPL no trae una variante negrita de la fuente 0, así que se
        # imprime el mismo texto corrido un dot: el solapamiento engrosa el
        # trazo. Es el recurso habitual en ZPL, y sin esto `bold` se
        # ignoraría en silencio y la etiqueta perdería la jerarquía que el
        # diseño le puso (el destinatario y el CP dejan de destacarse).
        commands += campo(_bold_offset(font_dots))
    return commands


def _bold_offset(font_dots):
    """Cuántos dots se corre la segunda pasada de un texto en negrita.

    Proporcional al cuerpo: un dot fijo se nota en 20 dots y desaparece en
    80. Nunca menos de 1, que es el mínimo que mueve algo.
    """
    return max(1, round(font_dots / 25))


def _shrink_to_fit_pt(text, font_pt, box_width_dots, dpmm):
    """Achica la letra hasta que el texto entre, como hace el PDF.

    **Es una aproximación**: se mide con Helvetica (lo que usa ReportLab) y
    la impresora usa su fuente 0, que tiene otras métricas. Sirve para que
    una dirección larga no pierda el número de calle, que es para lo que
    está ``shrink_to_fit``; el ajuste fino lo termina haciendo el ``^FB``.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    box_width_pt = box_width_dots / dpmm / MM_PER_INCH * POINTS_PER_INCH
    while font_pt > MIN_SHRINK_FONT_SIZE and stringWidth(text, "Helvetica", font_pt) > box_width_pt:
        font_pt = max(MIN_SHRINK_FONT_SIZE, font_pt - 0.5)
    return font_pt


# ---------------------------------------------------------------------------
# Códigos
# ---------------------------------------------------------------------------


def _qr_commands(entry, context, computed, width_cm, height_cm, dpmm):
    """QR nativo (``^BQ``). El firmware lo dibuja con módulos exactos, que es
    la diferencia más visible contra rasterizar un PDF."""
    data = _replace_placeholders(entry.get("data", DEFAULT_QR_DATA_MARKER), context, computed)
    if not data:
        return []

    left_pct, top_pct = entry.get("left", 0), entry.get("top", 0)
    size_cm = float(entry.get("size", DEFAULT_QR_SIZE_CM))
    avail_w_cm = float(width_cm) * (1 - float(left_pct) / 100)
    avail_h_cm = float(height_cm) * (1 - float(top_pct) / 100)
    size_cm, _ = _fit_size_cm(size_cm, avail_w_cm, avail_h_cm)
    if size_cm <= 0:
        return []

    magnification = _qr_magnification(data, cm_to_dots(size_cm, dpmm))
    x_dots, y_dots = percent_to_dots(left_pct, top_pct, width_cm, height_cm, dpmm)
    # ^BQN,2,<mag>: modelo 2 (el estándar). El dato lleva el prefijo
    # "<corrección><modo>," que ZPL espera dentro del ^FD.
    return [
        f"^FO{x_dots},{y_dots}",
        f"^BQN,2,{magnification}",
        "^FH\\",
        f"^FD{QR_ERROR_CORRECTION}A,{escape_field_data(data)}^FS",
    ]


def _qr_magnification(data, size_dots):
    """Cuántos dots mide cada módulo para que el QR se acerque a
    ``size_dots`` de lado.

    Se calcula la cantidad real de módulos con la misma librería que arma el
    QR del PDF, en vez de asumir una versión: un QR de 21 módulos y otro de
    57 con la misma magnificación salen de tamaños muy distintos, y el que
    importa es el que termina midiendo el lado pedido.
    """
    modules = _qr_module_count(data)
    if not modules:
        return QR_MAGNIFICATION_RANGE[0]
    return _clamp(size_dots // modules, *QR_MAGNIFICATION_RANGE)


def _qr_module_count(data):
    try:
        import qrcode

        code = qrcode.QRCode(
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            border=0,
        )
        code.add_data(data)
        code.make(fit=True)
        return code.modules_count
    except Exception:
        # Sin la cuenta exacta el QR sale de otro tamaño, pero sale y se
        # escanea: no es motivo para dejar la etiqueta sin código.
        logger.warning("No se pudo calcular el tamaño del QR; se usa la magnificación mínima.")
        return 0


def _barcode_commands(entry, context, computed, width_cm, height_cm, dpmm):
    """Código de barras 1D nativo (``^BC`` Code128 / ``^BE`` EAN-13).

    Lanza ``ValueError`` con el mismo criterio que
    ``label_rendering.build_barcode_drawing``: una simbología desconocida o
    un EAN-13 que no son 13 dígitos se cortan acá y no salen impresos como
    un código que ningún lector va a reconocer.
    """
    import re

    data = _replace_placeholders(entry.get("data", DEFAULT_BARCODE_DATA_MARKER), context, computed)
    symbology = (entry.get("symbology") or DEFAULT_BARCODE_SYMBOLOGY).lower()
    if symbology not in ("code128", "ean13"):
        raise ValueError(
            "Simbología de código de barras desconocida: '%s' (válidas: code128, ean13)." % symbology
        )
    if not data:
        return []
    if symbology == "ean13" and not re.fullmatch(r"\d{13}", data):
        raise ValueError(
            "EAN-13 requiere exactamente 13 dígitos numéricos (se recibió "
            "%r, %d caracter%s)." % (data, len(data), "es" if len(data) != 1 else "")
        )

    left_pct, top_pct = entry.get("left", 0), entry.get("top", 0)
    width_req_cm = float(entry.get("width", DEFAULT_BARCODE_WIDTH_CM))
    height_req_cm = float(entry.get("height", DEFAULT_BARCODE_HEIGHT_CM))
    avail_w_cm = float(width_cm) * (1 - float(left_pct) / 100)
    avail_h_cm = float(height_cm) * (1 - float(top_pct) / 100)
    bar_w_cm, bar_h_cm = _fit_size_cm((width_req_cm, height_req_cm), avail_w_cm, avail_h_cm)
    if bar_w_cm <= 0 or bar_h_cm <= 0:
        return []

    module_dots = _barcode_module_width(data, symbology, cm_to_dots(bar_w_cm, dpmm))
    height_dots = max(1, cm_to_dots(bar_h_cm, dpmm))
    show_text = "Y" if entry.get("show_text", True) else "N"
    x_dots, y_dots = percent_to_dots(left_pct, top_pct, width_cm, height_cm, dpmm)

    # ^BY define el ancho de módulo y la relación ancho/angosto; vale para
    # los ^B que siguen, así que se emite pegado al código.
    commands = [f"^FO{x_dots},{y_dots}", f"^BY{module_dots},3,{height_dots}"]
    if symbology == "ean13":
        commands.append(f"^BEN,{height_dots},{show_text},N")
    else:
        commands.append(f"^BCN,{height_dots},{show_text},N,N")
    commands.append("^FH\\")
    commands.append(f"^FD{escape_field_data(data)}^FS")
    return commands


def _barcode_module_width(data, symbology, width_dots):
    """Ancho de módulo (en dots) para acercarse al ancho pedido.

    La cuenta de módulos es la del formato: Code128 gasta 11 por carácter
    más el arranque, el dígito de control y el cierre; EAN-13 son siempre
    95. Si no llega ni con 1 dot por módulo, queda en 1 y el código sale más
    ancho de lo pedido — preferible a uno tan angosto que no se lea.
    """
    if symbology == "ean13":
        modules = 95
    else:
        modules = 11 * (len(data) + 3) + 2
    return _clamp(width_dots // modules, *BARCODE_MODULE_RANGE)


# ---------------------------------------------------------------------------
# Logo
# ---------------------------------------------------------------------------


def _logo_commands(entry, logo_file, width_cm, height_cm, dpmm):
    """El logo como imagen monocroma (``^GF``).

    Una térmica no tiene grises: solo quema el punto o no lo quema. La
    imagen se convierte a blanco y negro con difuminado (Floyd-Steinberg),
    que es lo que da la ilusión de medios tonos en una impresión de un bit.
    Si el archivo no se puede leer no se dibuja nada, igual que en el PDF.
    """
    try:
        from PIL import Image

        if hasattr(logo_file, "seek"):
            logo_file.seek(0)
        image = Image.open(logo_file)
        image.load()
    except Exception:
        logger.warning("No se pudo leer el logo para el ZPL; se omite.")
        return []

    box_cm = _box_size_cm(width_cm, height_cm)
    aspect = (image.width / image.height) if image.height else 1
    if aspect >= 1:
        draw_w_cm, draw_h_cm = box_cm, box_cm / aspect
    else:
        draw_w_cm, draw_h_cm = box_cm * aspect, box_cm

    target_w = max(1, cm_to_dots(draw_w_cm, dpmm))
    target_h = max(1, cm_to_dots(draw_h_cm, dpmm))

    try:
        # Sobre blanco: un PNG con transparencia, aplanado a secas, deja el
        # fondo en negro y sale un rectángulo quemado.
        if image.mode in ("RGBA", "LA", "P"):
            image = image.convert("RGBA")
            fondo = Image.new("RGBA", image.size, (255, 255, 255, 255))
            image = Image.alpha_composite(fondo, image)
        image = image.convert("L").resize((target_w, target_h))
        image = image.convert("1")
    except Exception:
        logger.warning("No se pudo convertir el logo a monocromo; se omite.")
        return []

    payload, bytes_per_row = _pack_monochrome(image)
    total = len(payload) // 2
    x_dots, y_dots = percent_to_dots(entry.get("left", 0), entry.get("top", 0), width_cm, height_cm, dpmm)
    return [
        f"^FO{x_dots},{y_dots}",
        f"^GFA,{total},{total},{bytes_per_row},{payload}^FS",
    ]


def _pack_monochrome(image):
    """La imagen de 1 bit como hexadecimal, fila por fila.

    ZPL empaqueta 8 píxeles por byte y **1 es tinta**, al revés del modo
    "1" de Pillow, donde 255 es blanco. Cada fila se rellena hasta
    completar el byte.
    """
    width, height = image.size
    bytes_per_row = (width + 7) // 8
    pixels = image.load()
    filas = []
    for y in range(height):
        fila = bytearray(bytes_per_row)
        for x in range(width):
            if not pixels[x, y]:  # 0 = negro en Pillow = punto quemado
                fila[x // 8] |= 0x80 >> (x % 8)
        filas.append(fila.hex().upper())
    return "".join(filas), bytes_per_row


# ---------------------------------------------------------------------------
# Decoraciones
# ---------------------------------------------------------------------------


def _decoration_commands(design, width_cm, height_cm, dpmm):
    """Recuadro de corte y líneas separadoras, con los mismos números que
    ``label_rendering._draw_decorations``."""
    border = design.get("border")
    lines = design.get("lines")
    if not isinstance(border, dict) and not isinstance(lines, list):
        return []

    thickness = max(1, points_to_dots(DECORATION_LINE_WIDTH_PT, dpmm))
    width_dots = cm_to_dots(width_cm, dpmm)
    height_dots = cm_to_dots(height_cm, dpmm)
    commands = []

    if isinstance(border, dict):
        inset = cm_to_dots(BORDER_INSET_CM, dpmm)
        box_w = width_dots - 2 * inset
        box_h = height_dots - 2 * inset
        if box_w > 0 and box_h > 0:
            if border.get("dashed"):
                commands += _dashed_rect(inset, inset, box_w, box_h, thickness, dpmm)
            else:
                commands.append(f"^FO{inset},{inset}^GB{box_w},{box_h},{thickness}^FS")

    for line in lines or []:
        if not isinstance(line, dict):
            continue
        left_dots = cm_to_dots(float(line.get("left", 4)) / 100 * width_cm, dpmm)
        right_dots = cm_to_dots(float(line.get("right", 96)) / 100 * width_cm, dpmm)
        y_dots = cm_to_dots(float(line.get("top", 0)) / 100 * height_cm, dpmm)
        length = right_dots - left_dots
        if length <= 0:
            continue
        if line.get("dashed"):
            commands += _dashed_segment(left_dots, y_dots, length, True, thickness, dpmm)
        else:
            # Un ^GB de alto 0 es una línea horizontal del grosor pedido.
            commands.append(f"^FO{left_dots},{y_dots}^GB{length},0,{thickness}^FS")
    return commands


def _dashed_segment(x, y, length, horizontal, thickness, dpmm):
    """Una línea punteada como una sucesión de ``^GB`` cortos: ZPL no tiene
    trazo punteado, un ``^GB`` siempre sale sólido."""
    dash = max(1, cm_to_dots(DASH_MM / 10, dpmm))
    commands = []
    offset = 0
    while offset < length:
        largo = min(dash, length - offset)
        if horizontal:
            commands.append(f"^FO{x + offset},{y}^GB{largo},0,{thickness}^FS")
        else:
            commands.append(f"^FO{x},{y + offset}^GB0,{largo},{thickness}^FS")
        offset += dash * 2
    return commands


def _dashed_rect(x, y, width, height, thickness, dpmm):
    return (
        _dashed_segment(x, y, width, True, thickness, dpmm)
        + _dashed_segment(x, y + height, width, True, thickness, dpmm)
        + _dashed_segment(x, y, height, False, thickness, dpmm)
        + _dashed_segment(x + width, y, height, False, thickness, dpmm)
    )


# ---------------------------------------------------------------------------
# Entrada pública
# ---------------------------------------------------------------------------


def render_label_zpl(
    design,
    width_cm,
    height_cm,
    context=None,
    logo_file=None,
    computed=None,
    dpmm=DEFAULT_DPMM,
):
    """El ZPL de UNA etiqueta, como texto.

    Mismo contrato que ``label_rendering.render_label_pdf``: mismo
    ``design``, mismo ``context``, mismas reglas condicionales, y también
    puede lanzar ``ValueError`` por una simbología desconocida o un EAN-13
    inválido (el llamador decide si es un 400 o un ítem salteado del lote).

    ``dpmm`` es la densidad de la impresora en dots por milímetro: 8 para
    una de 203 dpi, 12 para una de 300. Errarle no rompe nada, pero la
    etiqueta sale a otra escala.
    """
    if dpmm not in SUPPORTED_DPMM:
        raise ValueError(
            "Densidad de impresión no soportada: %r (válidas: %s)."
            % (dpmm, ", ".join(str(value) for value in SUPPORTED_DPMM))
        )

    context = context or {}
    design = design or {}
    width_cm, height_cm = float(width_cm), float(height_cm)
    effective_design = apply_design_rules(design, context, computed)

    # Mismo tamaño de letra por defecto que el PDF, para que un diseño sin
    # font_size propio salga igual por los dos caminos.
    height_pt = height_cm * POINTS_PER_INCH / MM_PER_INCH * 10
    default_font_pt = max(6.0, height_pt * 0.035)

    commands = [
        "^XA",
        # ^CI28 = UTF-8. Sin esto "María" y "Gutiérrez" salen con basura:
        # el default de ZPL no es Unicode, y es el tipo de error que no
        # aparece probando con datos en inglés.
        "^CI28",
        f"^PW{cm_to_dots(width_cm, dpmm)}",
        f"^LL{cm_to_dots(height_cm, dpmm)}",
        # El origen del rótulo en 0,0: si la impresora quedó con un ^LH de
        # otro trabajo, todo saldría corrido.
        "^LH0,0",
    ]

    commands += _decoration_commands(effective_design, width_cm, height_cm, dpmm)

    logo_entry = effective_design.get("logo")
    if logo_entry and logo_file:
        commands += _logo_commands(logo_entry, logo_file, width_cm, height_cm, dpmm)

    qr_entry = effective_design.get("qr")
    if qr_entry:
        commands += _qr_commands(qr_entry, context, computed, width_cm, height_cm, dpmm)

    barcode_entry = effective_design.get("barcode")
    if barcode_entry:
        commands += _barcode_commands(barcode_entry, context, computed, width_cm, height_cm, dpmm)

    text_entries = [
        entry
        for key, entry in effective_design.items()
        if key not in ("logo", "qr", "barcode", *DECORATION_KEYS) and isinstance(entry, dict)
    ]
    extra_texts = effective_design.get("texts")
    if isinstance(extra_texts, list):
        text_entries.extend(entry for entry in extra_texts if isinstance(entry, dict))

    for entry in text_entries:
        commands += _text_commands(
            entry, context, computed, width_cm, height_cm, default_font_pt, dpmm
        )

    commands.append("^XZ")
    return "\n".join(commands)


def render_labels_zpl(items, dpmm=DEFAULT_DPMM):
    """Varias etiquetas en un solo archivo.

    Una térmica no tiene "hoja": cada ``^XA ... ^XZ`` es una etiqueta y el
    rollo avanza una por una. Por eso acá no hay nada parecido al layout A4
    del PDF (``batch_views._render_combined_pdf_a4``) — no hay espacio que
    aprovechar, el papel ya viene cortado a la medida.

    ``items`` es una lista de kwargs de ``render_label_zpl``.
    """
    return "\n".join(render_label_zpl(dpmm=dpmm, **item) for item in items)
