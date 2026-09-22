"""Convierte una plantilla más los datos de un envío en una lista de dibujo.

Es la única capa que toma decisiones. Acá se resuelve qué valor va en cada
elemento, qué hacer si falta, cómo se genera un QR, qué pasa si un nombre no
entra en su caja. Los backends de abajo solo pintan lo que salga de acá.

Que no importe reportlab ni Pillow es deliberado: se puede testear el
renderizado entero afirmando sobre primitivas, sin generar un archivo ni
comparar imágenes.

**Las medidas de texto se toman una sola vez acá**, con las métricas de la
familia elegida en el estilo, y valen para los dos backends. Así el truncado
se decide en un solo lugar y el PNG de la vista previa muestra exactamente el
mismo texto que va a salir impreso; si cada backend midiera con lo suyo, la
previsualización mentiría. Que la familia entre en la cuenta no es un detalle:
Courier es bastante más ancha que Helvetica al mismo cuerpo.
"""

import json
import logging

from reportlab.pdfbase import pdfmetrics

from apps.labels.styles import DEFAULT_STYLE, effective_style
from apps.labels.models import LayoutVariableType, LayoutElementType

from . import codes, fonts
from .primitives import Canvas, Image, Line, Rectangle, Text

logger = logging.getLogger(__name__)

PT_PER_MM = 72 / 25.4

# Por debajo de esto el texto deja de ser legible en un rótulo impreso; antes
# de achicar más, se trunca.
MIN_FONT_SIZE_PT = 4

# Lado mínimo de un módulo de QR impreso, en milímetros, para que un lector de
# mano común lo levante. Por debajo de 0.4 mm hace falta un escáner dedicado y
# una impresión muy limpia; el rótulo de una encomienda no es ninguna de las
# dos cosas. Es el número que convierte "el QR entró en la caja" en "el QR se
# puede leer", que no es lo mismo.
MIN_QR_MODULE_MM = 0.4

# Proporción de la altura de la fuente que queda por encima de la línea base.
# Es una aproximación (varía por tipografía) y solo se usa para centrar el
# texto verticalmente dentro de su caja, donde un error de medio punto no se
# nota.
ASCENT_FACTOR = 0.72


def resolve(layout, data=None, user=None):
    """Devuelve el :class:`Canvas` listo para dibujar.

    ``data`` es ``{codigo_de_variable: valor}``. Si es ``None`` se arma una
    **vista previa**: cada variable se dibuja con su etiqueta entre comillas
    angulares. Es lo que necesita el editor para mostrar el diseño sin tener
    un envío de verdad.

    ``user`` acota la búsqueda de imágenes a los documentos de quien pide
    el render: sin eso, mandar un id ajeno en los datos alcanzaría para
    incrustar el archivo de otra cuenta en el rótulo propio.
    """
    preview = data is None
    data = data or {}

    canvas = Canvas(
        width_mm=float(layout.width_mm),
        height_mm=float(layout.height_mm),
        dpi=layout.dpi,
        background_color=layout.metadata.get("color_fondo", "#ffffff"),
    )

    # Qué variables de esta plantilla son imágenes. Lo necesita el QR del
    # envío para dejarlas afuera: en los datos son ids de documentos de este
    # sistema, que no le dicen nada a quien escanea el paquete. Se calcula una
    # vez acá porque los elementos ya vienen prefetcheados y recorrerlos nada
    # cuesta, mientras que averiguarlo dentro del QR obligaría a otra consulta.
    image_codes = {
        e.variable.code
        for e in layout.elements.all()
        if e.element_type == LayoutElementType.VARIABLE
        and e.variable_id
        and e.variable.data_type == LayoutVariableType.IMAGE
    }

    # `elements.all()` ya viene ordenado por `order` y después por `id`
    # (Meta.ordering), o sea en orden de pintado: el fondo primero.
    for element in layout.elements.all():
        _resolve_element(
            element, canvas, data, preview, user, image_codes
        )

    return canvas


def _resolve_element(
    element, canvas, data, preview, user, image_codes=frozenset()
):
    """Agrega al lienzo las primitivas que produce un elemento."""
    style = effective_style(element.style)
    box = (
        float(element.x_mm),
        float(element.y_mm),
        float(element.width_mm),
        float(element.height_mm),
    )

    if element.element_type == LayoutElementType.LINE:
        canvas.primitives.append(
            Line(*box, stroke_width_mm=style["grosor_mm"], color=style["color"])
        )
        return

    if element.element_type == LayoutElementType.BOX:
        canvas.primitives.append(
            Rectangle(*box, stroke_width_mm=style["grosor_mm"], color=style["color"])
        )
        return

    if element.element_type == LayoutElementType.STATIC_TEXT:
        # Se reporta por id y no por su contenido porque el aviso viaja en una
        # cabecera HTTP separada por comas: un texto con acentos o con una coma
        # la rompería. Con el id, el cliente encuentra el elemento en la
        # plantilla que ya tiene.
        _add_text(
            canvas, box, element.content, style,
            tag=f"texto_estatico#{element.pk}",
        )
        return

    # A partir de acá, element_type == variable.
    variable = element.variable
    value = data.get(variable.code)

    if preview:
        _add_placeholder(canvas, box, variable, style)
        return

    # El QR del envío es el único que no espera un dato propio: su contenido lo
    # arma con los de todos los demás. Por eso se atiende antes del control de
    # "valor vacío", que para él no significa nada.
    if variable.data_type == LayoutVariableType.QR_SHIPMENT:
        _add_shipment_qr(canvas, box, variable, data, image_codes)
        return

    if value in (None, ""):
        # Un campo vacío no es un error —la plantilla puede tener opcionales—
        # pero quien imprime 200 rótulos debería enterarse antes.
        if variable.code not in canvas.missing:
            canvas.missing.append(variable.code)
        return

    if variable.data_type == LayoutVariableType.QR:
        _add_qr(canvas, box, variable.code, str(value))
    elif variable.data_type == LayoutVariableType.BARCODE:
        png = codes.generate_barcode(value)
        if png is None:
            canvas.missing.append(variable.code)
        else:
            canvas.primitives.append(Image(*box, png=png))
    elif variable.data_type == LayoutVariableType.IMAGE:
        _add_image(canvas, box, variable, value, user)
    else:
        _add_text(canvas, box, str(value), style, tag=variable.code)


def _add_qr(canvas, box, code, content):
    """Pega un QR y avisa si le quedaron los módulos demasiado finos.

    Que un QR entre en su caja no quiere decir que se pueda leer: cuanto más
    datos lleva, más módulos tiene, y con la caja fija cada módulo se achica.
    Pasado cierto punto el código sale impecable en pantalla y ningún lector
    lo levanta. Como no rompe nada, sin este aviso se descubre con el paquete
    ya despachado.
    """
    png, modules = codes.generate_qr(content)
    canvas.primitives.append(Image(*box, png=png))

    # El QR es cuadrado y se pega centrado conservando su proporción, así que
    # el lado que manda es el menor de la caja.
    side_mm = min(box[2], box[3])
    module_mm = side_mm / modules if modules else 0
    if module_mm < MIN_QR_MODULE_MM:
        warning = f"qr_denso:{code}"
        if warning not in canvas.warnings:
            canvas.warnings.append(warning)
        logger.info(
            "QR %s: %s módulos en %.1f mm dan %.2f mm por módulo (mínimo %.2f)",
            code, modules, side_mm, module_mm, MIN_QR_MODULE_MM,
        )


def shipment_qr_payload(data, exclude=()):
    """Arma el contenido del QR del envío: un JSON compacto con todo el dato.

    Decisiones y por qué:

    - **JSON y no texto suelto** porque del otro lado hay un sistema leyendo,
      no una persona. Un WMS que recibe ``{"destinatario":"..."}`` sabe qué es
      cada cosa; uno que recibe tres líneas sueltas tiene que adivinar.
    - **Sin espacios** (``separators``): cada carácter de más son módulos de
      más, y los módulos de más son lo que vuelve ilegible el código.
    - **``ensure_ascii=False``** por lo mismo: "Gómez" son seis bytes en UTF-8
      y catorce caracteres si se escapa como ``Gómez``. El QR codifica
      bytes, así que escapar solo agranda.
    - **Ordenado por clave** para que el mismo envío produzca siempre el mismo
      código: si no, dos impresiones del mismo rótulo darían QR distintos y
      cualquier comparación se vuelve imposible.

    ``exclude`` deja afuera lo que no tiene sentido codificar: el propio QR y
    las imágenes, que en los datos son ids de documentos de este sistema y no
    significan nada para quien escanea el paquete.
    """
    useful = {
        key: str(value)
        for key, value in sorted(data.items())
        if key not in exclude and value not in (None, "")
    }
    return json.dumps(useful, ensure_ascii=False, separators=(",", ":"))


def _add_shipment_qr(canvas, box, variable, data, image_codes=frozenset()):
    """QR que lleva adentro todos los datos del envío, no un número suelto."""
    content = shipment_qr_payload(data, {variable.code} | set(image_codes))

    # Un JSON vacío ("{}") es un QR que no dice nada: se reporta como dato
    # faltante, igual que cualquier otra variable sin valor.
    if len(content) <= 2:
        if variable.code not in canvas.missing:
            canvas.missing.append(variable.code)
        return

    _add_qr(canvas, box, variable.code, content)


def _add_text(canvas, box, text, style, tag):
    """Agrega un texto, truncándolo si no entra en su caja."""
    x, y, width, height = box
    font_size = float(style["tamano_pt"])
    bold, italic = bool(style["negrita"]), bool(style["cursiva"])
    font_family = style.get("fuente")

    truncated_text, was_truncated = _truncate(text, width, font_size, bold, italic, font_family)
    if was_truncated and tag and tag not in canvas.truncated:
        canvas.truncated.append(tag)

    canvas.primitives.append(
        Text(
            x_mm=x, y_mm=y, width_mm=width, height_mm=height,
            text=truncated_text,
            font_size_pt=font_size,
            bold=bold,
            italic=italic,
            color=style["color"],
            alignment=style["alineacion"],
            font_family=font_family,
            truncated=was_truncated,
        )
    )


def _add_placeholder(canvas, box, variable, style):
    """Dibuja el hueco de una variable en modo vista previa.

    Los datos gráficos (QR, imágenes) se representan con un marco y la
    etiqueta adentro, para que se vea el espacio que van a ocupar sin tener
    que generar nada.
    """
    x, y, width, height = box

    if variable.data_type in (
        LayoutVariableType.QR, LayoutVariableType.QR_SHIPMENT,
        LayoutVariableType.BARCODE, LayoutVariableType.IMAGE,
    ):
        canvas.primitives.append(
            Rectangle(x, y, width, height, stroke_width_mm=0.2, color="#999999")
        )
        # La etiqueta se centra en el marco, con un cuerpo que no se pase del
        # alto disponible.
        font_size = max(min(height * PT_PER_MM * 0.35, 10), MIN_FONT_SIZE_PT)
        placeholder_style = dict(style, tamano_pt=font_size, alineacion="centro", color="#888888")
        _add_text(canvas, box, variable.label, placeholder_style, tag=None)
        return

    _add_text(canvas, box, f"«{variable.label}»", style, tag=None)


def _add_image(canvas, box, variable, value, user):
    """Resuelve una variable de tipo imagen contra un ``UploadedLabelFile``.

    ``value`` es el id de un documento ya subido. La búsqueda se acota al
    usuario que pide el render: un id ajeno no debería poder incrustarse en el
    rótulo propio.
    """
    from apps.documents.models import UploadedLabelFile

    if user is None:
        canvas.missing.append(variable.code)
        return

    try:
        document = UploadedLabelFile.objects.get(pk=int(value), uploaded_by=user)
    except (UploadedLabelFile.DoesNotExist, TypeError, ValueError):
        logger.info("No se encontró el documento %r para %s", value, variable.code)
        canvas.missing.append(variable.code)
        return

    if not document.is_image:
        # Un PDF no se puede pegar dentro de un rótulo como si fuera un logo.
        canvas.missing.append(variable.code)
        return

    with document.file.open("rb") as f:
        canvas.primitives.append(Image(*box, png=f.read()))


# ---------------------------------------------------------------------------
# Medición y truncado
# ---------------------------------------------------------------------------


def text_width_mm(text, font_size_pt, bold=False, italic=False, font_family=None):
    """Ancho que ocupa un texto, en milímetros.

    Usa las métricas reales de la fuente y no un promedio por carácter: con
    ancho variable, "MMMM" ocupa más del doble que "iiii", y contar caracteres
    haría que un texto en mayúsculas se desborde igual.

    Se mide con la familia que se va a imprimir: Courier es bastante más ancha
    que Helvetica al mismo cuerpo, así que medir todo con una sola haría que
    un texto en monoespaciada se desborde sin que nadie lo reporte.
    """
    points = pdfmetrics.stringWidth(
        text, fonts.pdf_font_name(bold, italic, font_family), font_size_pt
    )
    return points / PT_PER_MM


def _truncate(text, width_mm, font_size_pt, bold, italic, font_family=None):
    """Devuelve ``(texto, hubo_recorte)`` recortando con "…" si no entra."""
    if not text or text_width_mm(text, font_size_pt, bold, italic, font_family) <= width_mm:
        return text, False

    # Se saca un carácter por vez desde el final hasta que el texto más los
    # puntos suspensivos entren. Búsqueda binaria sería más rápida, pero los
    # textos de un rótulo tienen decenas de caracteres, no miles.
    truncated = text
    while truncated:
        truncated = truncated[:-1]
        candidate = truncated.rstrip() + "…"
        if text_width_mm(candidate, font_size_pt, bold, italic, font_family) <= width_mm:
            return candidate, True

    # La caja es tan angosta que no entra ni un carácter con puntos.
    return "…", True


def baseline_mm(y_mm, height_mm, font_size_pt):
    """Y de la línea base para centrar verticalmente el texto en su caja.

    Los dos backends necesitan lo mismo, así que se calcula una sola vez acá.
    Se centra en vez de alinear arriba porque las cajas que produce el
    importador suelen ser más altas que el texto, y un texto pegado al borde
    superior queda visualmente suelto.
    """
    text_height_mm = font_size_pt * ASCENT_FACTOR / PT_PER_MM
    return y_mm + (height_mm + text_height_mm) / 2


def color_rgb(hex_color):
    """Convierte ``#rrggbb`` a una tupla ``(r, g, b)`` de 0 a 1."""
    value = (hex_color or DEFAULT_STYLE["color"]).lstrip("#")
    try:
        return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (0.0, 0.0, 0.0)
