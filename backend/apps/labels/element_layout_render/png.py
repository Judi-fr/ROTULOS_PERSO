"""Backend de dibujo a PNG, con Pillow.

Es el gemelo de ``pdf.py`` y sirve para la vista previa del editor: un PNG se
muestra en un ``<img>`` sin plugins ni visores.

Dos diferencias con el backend PDF:

- **El origen coincide** con el del modelo (arriba a la izquierda, Y hacia
  abajo), así que no hay ninguna inversión de eje. Es el backend fácil.
- **Se trabaja en píxeles**, no en milímetros. La conversión sale de
  ``mm_to_px`` —la misma función que usan ``ElementLayout.width_px`` y el
  resto del sistema—, así que el lienzo y lo que va encima no pueden quedar
  corridos entre sí.

Para imprimir conviene el PDF: es vectorial, el texto queda seleccionable y no
se pixela al ampliar. El PNG es para mirar en pantalla.
"""

import io

from PIL import Image, ImageDraw, ImageFont

from apps.labels.models import mm_to_px

from . import fonts
from .primitives import Image as ImagePrimitive, Line, Rectangle, Text
from .resolution import PT_PER_MM, baseline_mm


def draw(canvas, dpi=None):
    """Devuelve los bytes de un PNG con el lienzo dibujado.

    ``dpi`` sobreescribe el de la plantilla, para pedir una vista previa
    liviana (150) sin tocar la plantilla ni el PDF de impresión.
    """
    dpi = dpi or canvas.dpi
    width = mm_to_px(canvas.width_mm, dpi)
    height = mm_to_px(canvas.height_mm, dpi)

    image = Image.new("RGB", (max(width, 1), max(height, 1)), canvas.background_color)
    draw_ctx = ImageDraw.Draw(image)

    for primitive in canvas.primitives:
        if isinstance(primitive, Text):
            _draw_text(draw_ctx, primitive, dpi)
        elif isinstance(primitive, ImagePrimitive):
            _draw_image(image, primitive, dpi)
        elif isinstance(primitive, Line):
            _draw_line(draw_ctx, primitive, dpi)
        elif isinstance(primitive, Rectangle):
            _draw_rectangle(draw_ctx, primitive, dpi)

    buffer = io.BytesIO()
    # dpi en la cabecera del PNG: así un visor que lo respete muestra el
    # tamaño físico correcto en vez de suponer 96 puntos por pulgada.
    image.save(buffer, format="PNG", dpi=(dpi, dpi))
    return buffer.getvalue()


def _font(font_size_pt, dpi, bold, italic, family=None):
    """Carga el TTF al tamaño en píxeles que corresponde a ese DPI."""
    height_px = max(mm_to_px(font_size_pt / PT_PER_MM, dpi), 1)
    return ImageFont.truetype(fonts.ttf_path(bold, italic, family), height_px)


def _draw_text(draw_ctx, t, dpi):
    font = _font(t.font_size_pt, dpi, t.bold, t.italic, t.font_family)
    base_mm = baseline_mm(t.y_mm, t.height_mm, t.font_size_pt)

    if t.alignment == "centro":
        x_mm, anchor = t.x_mm + t.width_mm / 2, "ms"
    elif t.alignment == "derecha":
        x_mm, anchor = t.x_mm + t.width_mm, "rs"
    else:
        x_mm, anchor = t.x_mm, "ls"

    # El anclaje termina en "s" (baseline) en los tres casos, así que Pillow
    # sitúa el texto por la misma referencia que reportlab y los dos backends
    # producen la misma ubicación vertical.
    draw_ctx.text(
        (mm_to_px(x_mm, dpi), mm_to_px(base_mm, dpi)),
        t.text,
        font=font,
        fill=t.color,
        anchor=anchor,
    )


def _draw_image(destination, img, dpi):
    width = max(mm_to_px(img.width_mm, dpi), 1)
    height = max(mm_to_px(img.height_mm, dpi), 1)

    with Image.open(io.BytesIO(img.png)) as source:
        source = source.convert("RGBA")
        # thumbnail respeta la proporción: un QR estirado deja de escanearse.
        source.thumbnail((width, height), Image.LANCZOS)
        # Centrado dentro de la caja, igual que el anchor="c" del PDF.
        x = mm_to_px(img.x_mm, dpi) + (width - source.width) // 2
        y = mm_to_px(img.y_mm, dpi) + (height - source.height) // 2
        destination.paste(source, (x, y), source)


def _draw_line(draw_ctx, line, dpi):
    stroke_width = max(mm_to_px(line.stroke_width_mm, dpi), 1)

    if line.is_horizontal:
        y = mm_to_px(line.y_mm + line.height_mm / 2, dpi)
        endpoints = [
            (mm_to_px(line.x_mm, dpi), y),
            (mm_to_px(line.x_mm + line.width_mm, dpi), y),
        ]
    else:
        x = mm_to_px(line.x_mm + line.width_mm / 2, dpi)
        endpoints = [
            (x, mm_to_px(line.y_mm, dpi)),
            (x, mm_to_px(line.y_mm + line.height_mm, dpi)),
        ]

    draw_ctx.line(endpoints, fill=line.color, width=stroke_width)


def _draw_rectangle(draw_ctx, r, dpi):
    draw_ctx.rectangle(
        [
            (mm_to_px(r.x_mm, dpi), mm_to_px(r.y_mm, dpi)),
            (mm_to_px(r.x_mm + r.width_mm, dpi), mm_to_px(r.y_mm + r.height_mm, dpi)),
        ],
        outline=r.color,
        width=max(mm_to_px(r.stroke_width_mm, dpi), 1),
    )
