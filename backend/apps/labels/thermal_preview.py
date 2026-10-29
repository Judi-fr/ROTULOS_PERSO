"""Vista térmica del editor (fase 4): el PDF de la vista previa como lo
imprime una Zebra — 1 bit, a la densidad de la impresora, con la misma regla
que las formas en ZPL (``design_shapes.thermal_tone``): luminancia menor a
``THERMAL_BLACK_BELOW`` → negro, mayor a ``THERMAL_WHITE_ABOVE`` → nada, en
medio la trama Bayer 4×4 de ``shapes_zpl``.

Es una aproximación del ZPL: la Zebra dibuja la sans con su fuente propia y
el texto claro sale con ``^FR``; acá todo sale del PDF. Sirve para ver qué
grises se pierden o se vuelven trama, que es lo que el comerciante no puede
adivinar mirando la pantalla a color.
"""

from io import BytesIO

import pypdfium2
from PIL import Image, ImageChops

from .design_shapes import THERMAL_BLACK_BELOW, THERMAL_WHITE_ABOVE
from .shapes_zpl import BAYER_4X4

POINTS_PER_INCH = 72
MM_PER_INCH = 25.4


def _threshold_tile():
    """4×4 de luminancias: un píxel sale negro si su luminancia es menor que
    la de su celda. Es la misma regla que ``shapes_zpl`` (tinta cuando
    ``bayer < tono*16``, con el tono de ``thermal_tone``: 0 en
    ``THERMAL_WHITE_ABOVE``, 1 en ``THERMAL_BLACK_BELOW``) despejada para la
    luminancia."""
    span = THERMAL_WHITE_ABOVE - THERMAL_BLACK_BELOW
    tile = Image.new("L", (4, 4))
    for y, row in enumerate(BAYER_4X4):
        for x, level in enumerate(row):
            tile.putpixel((x, y), round(THERMAL_BLACK_BELOW + span * (1 - level / 16)))
    return tile


def _thresholds(size):
    """La trama del tamaño del rótulo: una fila de celdas y después esa fila
    repetida hacia abajo (de a una celda, un rótulo grande eran cientos de
    miles de llamadas)."""
    width, height = size
    tile = _threshold_tile()
    strip = Image.new("L", (width, 4))
    for x in range(0, width, 4):
        strip.paste(tile, (x, 0))
    image = Image.new("L", size)
    for y in range(0, height, 4):
        image.paste(strip, (0, y))
    return image


def thermal_png(pdf_bytes, dpmm=8):
    document = pypdfium2.PdfDocument(pdf_bytes)
    try:
        page = document[0]
        scale = dpmm * MM_PER_INCH / POINTS_PER_INCH
        # Tantos dots como la impresora: pdfium redondea para arriba y deja
        # un píxel de más, que se corta.
        size = (round(page.get_width() * scale), round(page.get_height() * scale))
        gray = page.render(scale=scale, grayscale=True).to_pil().convert("L").crop((0, 0, *size))
    finally:
        document.close()
    thresholds = _thresholds(gray.size)
    # > 0 donde la luminancia queda por debajo de su umbral → tinta.
    ink = ImageChops.subtract(thresholds, gray)
    image = ink.point(lambda value: 0 if value > 0 else 255).convert("1")
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
