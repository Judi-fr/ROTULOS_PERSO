"""Backend de dibujo a PDF, con reportlab.

Este módulo no decide nada: recibe lienzos ya resueltos y los pinta.

**La única complicación real es el sistema de coordenadas.** reportlab hereda
del estándar PostScript el origen en la esquina **inferior** izquierda, con la
Y creciendo hacia arriba. El modelo de datos —igual que CSS, igual que
cualquier editor gráfico— pone el origen **arriba** a la izquierda con la Y
creciendo hacia abajo.

Sin convertir, un rótulo sale dado vuelta en espejo vertical: lo que iba
arriba aparece abajo. Y como no es un error que rompa nada, se descubre recién
mirando el PDF.

Toda la conversión pasa por :func:`_y`, que además de invertir el eje corre el
punto de referencia al borde inferior de la caja, que es lo que esperan las
primitivas de reportlab.
"""

import io

from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as canvas_pdf

from . import fonts
from .primitives import Image, Line, Rectangle, Text
from .resolution import baseline_mm, color_rgb


def draw(canvases):
    """Devuelve los bytes de un PDF con un lienzo por página.

    Acepta una lista para poder resolver el caso real de un depósito que
    despacha doscientos paquetes: un solo PDF, un rótulo por página, una sola
    orden de impresión.
    """
    buffer = io.BytesIO()
    canvases = list(canvases)
    if not canvases:
        raise ValueError("No hay nada para renderizar.")

    canvas = canvases[0]
    c = canvas_pdf.Canvas(
        buffer, pagesize=(canvas.width_mm * mm, canvas.height_mm * mm)
    )

    for index, canvas in enumerate(canvases):
        if index:
            # Cada rótulo puede tener su propio tamaño de papel.
            c.setPageSize((canvas.width_mm * mm, canvas.height_mm * mm))
        _draw_page(c, canvas)
        c.showPage()

    c.save()
    return buffer.getvalue()


def _draw_page(c, canvas):
    _background(c, canvas)

    for primitive in canvas.primitives:
        if isinstance(primitive, Text):
            _draw_text(c, canvas, primitive)
        elif isinstance(primitive, Image):
            _draw_image(c, canvas, primitive)
        elif isinstance(primitive, Line):
            _draw_line(c, canvas, primitive)
        elif isinstance(primitive, Rectangle):
            _draw_rectangle(c, canvas, primitive)


def _y(canvas, y_mm, height_mm=0):
    """Convierte una Y de arriba-abajo (modelo) a abajo-arriba (PDF).

    ``y_mm`` es el borde **superior** de la caja en el modelo; reportlab quiere
    el borde **inferior** contado desde abajo del papel::

        modelo:  y_mm ─────┬──────┐
                           │ caja │ height_mm
                    ───────┴──────┘
        PDF:     alto_total - y_mm - height_mm  ← lo que devuelve esta función
    """
    return (canvas.height_mm - y_mm - height_mm) * mm


def _background(c, canvas):
    """Pinta el color de fondo del rótulo, si no es blanco.

    Un PDF es transparente por defecto y sobre papel blanco eso ya es blanco,
    así que el caso normal no necesita ningún rectángulo.
    """
    if (canvas.background_color or "#ffffff").lower() in ("#ffffff", "#fff"):
        return
    c.setFillColorRGB(*color_rgb(canvas.background_color))
    c.rect(0, 0, canvas.width_mm * mm, canvas.height_mm * mm, stroke=0, fill=1)


def _draw_text(c, canvas, t):
    c.setFont(fonts.pdf_font_name(t.bold, t.italic, t.font_family), t.font_size_pt)
    c.setFillColorRGB(*color_rgb(t.color))

    base_mm = baseline_mm(t.y_mm, t.height_mm, t.font_size_pt)
    y = _y(canvas, base_mm)

    if t.alignment == "centro":
        c.drawCentredString((t.x_mm + t.width_mm / 2) * mm, y, t.text)
    elif t.alignment == "derecha":
        c.drawRightString((t.x_mm + t.width_mm) * mm, y, t.text)
    else:
        # "justificado" cae acá: justificar una sola línea es alinear a la
        # izquierda, y los elementos de un rótulo son de una línea.
        c.drawString(t.x_mm * mm, y, t.text)


def _draw_image(c, canvas, img):
    c.drawImage(
        ImageReader(io.BytesIO(img.png)),
        img.x_mm * mm,
        _y(canvas, img.y_mm, img.height_mm),
        width=img.width_mm * mm,
        height=img.height_mm * mm,
        # Un QR deformado deja de ser escaneable, así que la imagen se ajusta
        # dentro de la caja conservando su proporción en vez de estirarse.
        preserveAspectRatio=True,
        anchor="c",
        mask="auto",
    )


def _draw_line(c, canvas, line):
    c.setStrokeColorRGB(*color_rgb(line.color))
    c.setLineWidth(line.stroke_width_mm * mm)

    if line.is_horizontal:
        # Se dibuja por el centro vertical de la caja: el grosor crece hacia
        # ambos lados y la línea queda donde el diseñador la puso.
        y = _y(canvas, line.y_mm + line.height_mm / 2)
        c.line(line.x_mm * mm, y, (line.x_mm + line.width_mm) * mm, y)
    else:
        x = (line.x_mm + line.width_mm / 2) * mm
        c.line(x, _y(canvas, line.y_mm), x, _y(canvas, line.y_mm + line.height_mm))


def _draw_rectangle(c, canvas, r):
    c.setStrokeColorRGB(*color_rgb(r.color))
    c.setLineWidth(r.stroke_width_mm * mm)
    c.rect(
        r.x_mm * mm,
        _y(canvas, r.y_mm, r.height_mm),
        r.width_mm * mm,
        r.height_mm * mm,
        stroke=1,
        fill=0,
    )
