"""Íconos del rótulo (fase 2 del editor): una forma más
(``shapes[]`` con ``kind: "icon"``, ver design_shapes.py), con su geometría en
``icons.json`` — una grilla de 24×24 con primitivas
``{"poly": [[x, y], ...], "closed", "fill", "width"}`` y
``{"circle": [cx, cy, r], "fill", "width"}``. El editor dibuja la copia
idéntica ``frontend/assets/data/label_icons.json`` (lo verifica
``test_icons_json_copies_identical``).

El ícono se dibuja en el cuadrado más grande que entra en su caja, centrado:
no se deforma aunque la caja no sea cuadrada. En PDF son vectores; en ZPL,
un gráfico ``^GF`` dibujado con Pillow (negro, salvo blanco: como los bordes).
"""

import json
from functools import lru_cache
from pathlib import Path

ICONS_FILE = Path(__file__).resolve().parent / "icons.json"


@lru_cache(maxsize=1)
def _data():
    return json.loads(ICONS_FILE.read_text(encoding="utf-8"))


ICON_NAMES = tuple(_data()["icons"])
VIEWBOX = _data()["viewbox"]


def icon_box(left, top, width, height):
    """``(x, y, side)``: el cuadrado contenido y centrado en la caja (mismas
    unidades que recibe; ``y`` es el borde de arriba)."""
    side = min(width, height)
    return left + (width - side) / 2, top + (height - side) / 2, side


def draw_icon_pdf(pdf, name, x, y_top, side, rgb):
    """Dibuja el ícono en el cuadrado de lado ``side`` cuya esquina de arriba
    a la izquierda es ``(x, y_top)`` (coordenadas de la página, y hacia
    arriba)."""
    scale = side / VIEWBOX
    pdf.saveState()
    pdf.setFillColorRGB(*rgb)
    pdf.setStrokeColorRGB(*rgb)
    pdf.setLineJoin(1)
    pdf.setLineCap(1)
    for item in _data()["icons"].get(name, []):
        fill = bool(item.get("fill"))
        stroke = not fill
        if stroke:
            pdf.setLineWidth(item.get("width", 1.5) * scale)
        if "circle" in item:
            cx, cy, radius = item["circle"]
            pdf.circle(x + cx * scale, y_top - cy * scale, radius * scale, stroke=int(stroke), fill=int(fill))
            continue
        path = pdf.beginPath()
        points = item["poly"]
        path.moveTo(x + points[0][0] * scale, y_top - points[0][1] * scale)
        for px, py in points[1:]:
            path.lineTo(x + px * scale, y_top - py * scale)
        if item.get("closed"):
            path.close()
        pdf.drawPath(path, stroke=int(stroke), fill=int(fill))
    pdf.restoreState()


def icon_bitmap(name, side_dots):
    """El ícono a 1 bit (1 = tinta) en un cuadrado de ``side_dots``. Se
    dibuja al cuádruple y se reduce, para que las diagonales no salgan
    escalonadas."""
    from PIL import Image, ImageDraw

    side = max(1, int(side_dots))
    big = side * 4
    scale = big / VIEWBOX
    image = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(image)
    for item in _data()["icons"].get(name, []):
        width = max(1, round(item.get("width", 1.5) * scale))
        if "circle" in item:
            cx, cy, radius = (value * scale for value in item["circle"])
            box = (cx - radius, cy - radius, cx + radius, cy + radius)
            if item.get("fill"):
                draw.ellipse(box, fill=255)
            else:
                draw.ellipse(box, outline=255, width=width)
            continue
        points = [(px * scale, py * scale) for px, py in item["poly"]]
        if item.get("fill"):
            draw.polygon(points, fill=255)
        else:
            if item.get("closed"):
                points = points + points[:1]
            draw.line(points, fill=255, width=width, joint="curve")
            for px, py in (points[0], points[-1]):
                # Puntas redondeadas, como el setLineCap(1) del PDF.
                draw.ellipse((px - width / 2, py - width / 2, px + width / 2, py + width / 2), fill=255)
    return image.resize((side, side), Image.LANCZOS).point(lambda value: 1 if value >= 128 else 0, mode="1")
