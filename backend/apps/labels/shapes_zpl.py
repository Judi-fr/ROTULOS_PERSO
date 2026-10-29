"""Formas y cajas con contenido en ZPL (``design["shapes"]``, ver
design_shapes.py). Lo llama ``zpl.render_label_zpl`` después de las
decoraciones, en el mismo orden que el PDF (shapes_pdf.ordered_shapes).

Una térmica no tiene color (design_shapes.thermal_tone):
- Relleno negro: comandos propios (``^GB`` lleno, con el redondeo de esquinas
  de su 5.º parámetro; ``^GE`` para la elipse). Blanco: nada.
- Relleno gris: se dibuja la figura con Pillow, con trama Bayer 4×4 según el
  tono, y va como gráfico ``^GF`` comprimido (compresión ASCII de ZPL).
- Bordes y líneas: negros (una trama de 1-3 puntos de ancho no se lee) salvo
  que el color sea blanco. Punteado sobre esquinas redondeadas o elipse va
  dentro del gráfico; sobre un rectángulo recto, con ``^GB`` cortos.
- Texto de una caja: ``^FB`` con la alineación de la caja; si el color es
  claro, ``^FR`` (blanco donde pisa negro).
"""

import logging
import math

from .design_shapes import (
    DEFAULT_PADDING_MM,
    DEFAULT_STROKE_WIDTH_PT,
    resolve_color,
    stroke_color,
    thermal_text_is_white,
    thermal_tone,
)
from .shapes_pdf import DASH_PATTERNS, ordered_shapes, shape_box_pct, shape_is_hidden

logger = logging.getLogger(__name__)

BAYER_4X4 = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)
ALIGN_TO_ZPL = {"left": "L", "center": "C", "right": "R"}


def shape_commands(design, context, computed, width_cm, height_cm, default_font_pt, dpmm, logo_file=None,
                   warnings=None):
    from .zpl import cm_to_dots, points_to_dots

    palette = design.get("palette") if isinstance(design.get("palette"), dict) else None
    width_dots, height_dots = cm_to_dots(width_cm, dpmm), cm_to_dots(height_cm, dpmm)
    commands = []
    for shape in ordered_shapes(design):
        if shape.get("kind") not in ("rect", "ellipse", "line", "icon", "image") or shape_is_hidden(shape, context, computed):
            continue
        left, top, right, bottom = shape_box_pct(shape)
        x0, x1 = round(left / 100 * width_dots), round(right / 100 * width_dots)
        y0, y1 = round(top / 100 * height_dots), round(bottom / 100 * height_dots)
        thickness = max(1, points_to_dots(_num(shape.get("stroke_width"), DEFAULT_STROKE_WIDTH_PT), dpmm))
        if shape["kind"] == "icon":
            commands += _icon(shape, palette, x0, x1, y0, y1)
            continue
        if shape["kind"] == "image":
            commands += _image(shape, palette, x0, x1, y0, y1, thickness, dpmm, logo_file, warnings)
            continue
        if shape["kind"] == "line":
            commands += _line(shape, palette, x0, x1, y0, y1, thickness, dpmm)
            continue
        commands += _area(shape, palette, x0, x1, y0, y1, thickness, dpmm)
        commands += _box_text(shape, palette, context, computed, x0, x1, y0, y1, default_font_pt, dpmm)
    return commands


def _num(value, default):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _prints_black(color):
    """Borde o línea: negro salvo que el color sea blanco (o no haya)."""
    return bool(color) and thermal_tone(color)[0] != "white"


def _dash_dots(style, dpmm):
    from .zpl import points_to_dots

    on, off = DASH_PATTERNS[style]
    return max(1, points_to_dots(on, dpmm)), max(1, points_to_dots(off, dpmm))


def _dashed(x, y, length, horizontal, thickness, style, dpmm):
    on, off = _dash_dots(style, dpmm)
    commands, offset = [], 0
    while offset < length:
        piece = min(on, length - offset)
        if horizontal:
            commands.append(f"^FO{x + offset},{y}^GB{piece},0,{thickness}^FS")
        else:
            commands.append(f"^FO{x},{y + offset}^GB0,{piece},{thickness}^FS")
        offset += on + off
    return commands


def _line(shape, palette, x0, x1, y0, y1, thickness, dpmm):
    color = stroke_color(shape, palette, default="#000000")
    if not _prints_black(color):
        return []
    vertical = _num(shape.get("height"), 0) > 0
    length = (y1 - y0) if vertical else (x1 - x0)
    if length <= 0:
        return []
    style = shape.get("stroke_style")
    if style in DASH_PATTERNS:
        return _dashed(x0, y0, length, not vertical, thickness, style, dpmm)
    if vertical:
        return [f"^FO{x0},{y0}^GB0,{length},{thickness}^FS"]
    return [f"^FO{x0},{y0}^GB{length},0,{thickness}^FS"]


def _icon(shape, palette, x0, x1, y0, y1):
    """El ícono como gráfico; negro salvo que su color sea blanco (como los
    bordes: una trama sobre un dibujo chico no se lee)."""
    from .icons import icon_bitmap, icon_box

    color = resolve_color(shape.get("fill"), palette) or "#000000"
    if not _prints_black(color):
        return []
    x, y, side = icon_box(x0, y0, x1 - x0, y1 - y0)
    side = int(side)
    if side <= 0:
        return []
    rows, bytes_per_row = _rows(icon_bitmap(shape.get("icon"), side))
    total = bytes_per_row * side
    return [f"^FO{round(x)},{round(y)}", f"^GFA,{total},{total},{bytes_per_row},{compress_rows(rows)}^FS"]


def _image(shape, palette, x0, x1, y0, y1, thickness, dpmm, logo_file, warnings):
    """La imagen como gráfico monocromo (difuminado Floyd-Steinberg, como el
    logo) más su borde; mismas reglas que ``shapes_pdf._draw_image``."""
    from PIL import Image, ImageOps

    from .icons import icon_box
    from .label_images import prepare, resolve_source
    from .zpl import cm_to_dots

    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0:
        return []
    circle = bool(shape.get("circle"))
    radius_dots = 0 if circle else cm_to_dots(_num(shape.get("radius"), 0) / 10, dpmm)
    source = resolve_source(shape, logo_file)
    image = None
    if source is not None:
        try:
            image = prepare(source, width, height, shape.get("fit", "contain"), circle, radius_dots)
        except Exception:
            logger.warning("No se pudo preparar la imagen %r del rótulo.", shape.get("id"), exc_info=True)
            image = None
        finally:
            # Solo se cierra lo que abrió image_file, nunca el logo del llamador.
            if shape.get("image") != "logo":
                source.close()
    if image is None:
        if warnings is not None:
            warnings.append({"code": "image_missing", "field": shape.get("id", "")})
        return []
    # Sobre blanco: lo transparente no debe quemarse.
    flat = Image.alpha_composite(Image.new("RGBA", image.size, (255, 255, 255, 255)), image)
    ink = ImageOps.invert(flat.convert("L").convert("1").convert("L"))
    rows, bytes_per_row = _rows(ink)
    total = bytes_per_row * height
    commands = [f"^FO{x0},{y0}", f"^GFA,{total},{total},{bytes_per_row},{compress_rows(rows)}^FS"]
    stroke = stroke_color(shape, palette)
    if _prints_black(stroke):
        border = {k: shape.get(k) for k in ("stroke", "stroke_width", "stroke_style", "radius")}
        border["kind"] = "ellipse" if circle else "rect"
        bx0, by0, bx1, by1 = x0, y0, x1, y1
        if circle:
            bx, by, side = icon_box(x0, y0, width, height)
            bx0, by0, bx1, by1 = round(bx), round(by), round(bx + side), round(by + side)
        commands += _area(border, palette, bx0, bx1, by0, by1, thickness, dpmm)
    return commands


def _rounding(radius_dots, width, height):
    """El 5.º parámetro de ``^GB``: 0 (recto) a 8 (lo más redondo, radio =
    la mitad del lado menor)."""
    half = min(width, height) / 2
    if radius_dots <= 0 or half <= 0:
        return 0
    return max(0, min(8, round(8 * radius_dots / half)))


def _area(shape, palette, x0, x1, y0, y1, thickness, dpmm):
    from .zpl import cm_to_dots

    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0:
        return []
    fill = resolve_color(shape.get("fill"), palette)
    stroke = stroke_color(shape, palette)
    fill_tone = thermal_tone(fill) if fill else ("white", 0)
    has_stroke = _prints_black(stroke)
    style = shape.get("stroke_style") if shape.get("stroke_style") in DASH_PATTERNS else None
    radius_dots = cm_to_dots(_num(shape.get("radius"), 0) / 10, dpmm) if shape["kind"] == "rect" else 0
    ellipse = shape["kind"] == "ellipse"

    needs_graphic = fill_tone[0] == "gray" or (has_stroke and style and (ellipse or radius_dots > 0))
    if needs_graphic:
        return _graphic(x0, y0, width, height, ellipse, radius_dots, fill_tone, has_stroke, thickness, style, dpmm)

    commands = []
    if fill_tone[0] == "black":
        if ellipse:
            commands.append(f"^FO{x0},{y0}^GE{width},{height},{math.ceil(min(width, height) / 2)},B^FS")
        else:
            rounding = _rounding(radius_dots, width, height)
            commands.append(f"^FO{x0},{y0}^GB{width},{height},{min(width, height)},B,{rounding}^FS")
    if has_stroke:
        if ellipse:
            commands.append(f"^FO{x0},{y0}^GE{width},{height},{thickness},B^FS")
        elif style:
            commands += _dashed(x0, y0, width, True, thickness, style, dpmm)
            commands += _dashed(x0, y1 - thickness, width, True, thickness, style, dpmm)
            commands += _dashed(x0, y0, height, False, thickness, style, dpmm)
            commands += _dashed(x1 - thickness, y0, height, False, thickness, style, dpmm)
        else:
            rounding = _rounding(radius_dots, width, height)
            commands.append(f"^FO{x0},{y0}^GB{width},{height},{thickness},B,{rounding}^FS")
    return commands


def _graphic(x0, y0, width, height, ellipse, radius_dots, fill_tone, has_stroke, thickness, style, dpmm):
    """La figura dibujada con Pillow a 1 bit (1 = tinta) como ``^GF``."""
    from PIL import Image, ImageDraw

    mask = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    box = (0, 0, width - 1, height - 1)
    ink = Image.new("1", (width, height), 0)
    if fill_tone[0] != "white":
        if ellipse:
            draw.ellipse(box, fill=1)
        else:
            draw.rounded_rectangle(box, radius=radius_dots, fill=1)
        level = 1.0 if fill_tone[0] == "black" else fill_tone[1]
        threshold = level * 16
        pixels, mask_pixels = ink.load(), mask.load()
        for y in range(height):
            row = BAYER_4X4[y % 4]
            for x in range(width):
                if mask_pixels[x, y] and row[x % 4] < threshold:
                    pixels[x, y] = 1
    if has_stroke:
        outline = Image.new("1", (width, height), 0)
        outline_draw = ImageDraw.Draw(outline)
        if ellipse:
            outline_draw.ellipse(box, outline=1, width=thickness)
        else:
            outline_draw.rounded_rectangle(box, radius=radius_dots, outline=1, width=thickness)
        if style:
            # Punteado por ángulo alrededor del centro: la elipse y las
            # esquinas redondeadas no tienen un ^GB cortado que las siga.
            on, off = _dash_dots(style, dpmm)
            mean_radius = (width + height) / 4
            cx, cy = (width - 1) / 2, (height - 1) / 2
            out_pixels = outline.load()
            for y in range(height):
                for x in range(width):
                    if out_pixels[x, y]:
                        arc = (math.atan2(y - cy, x - cx) + math.pi) * mean_radius
                        if arc % (on + off) >= on:
                            out_pixels[x, y] = 0
        ink.paste(1, mask=outline)
    rows, bytes_per_row = _rows(ink)
    total = bytes_per_row * height
    return [f"^FO{x0},{y0}", f"^GFA,{total},{total},{bytes_per_row},{compress_rows(rows)}^FS"]


def _rows(image):
    width, height = image.size
    bytes_per_row = (width + 7) // 8
    pixels = image.load()
    rows = []
    for y in range(height):
        row = bytearray(bytes_per_row)
        for x in range(width):
            if pixels[x, y]:
                row[x // 8] |= 0x80 >> (x % 8)
        rows.append(row.hex().upper())
    return rows, bytes_per_row


def _repeat(count, char):
    """Compresión ASCII de ZPL: G-Y = 1-19 repeticiones, g-z = 20-400 de a
    20, seguidas del carácter."""
    out = []
    while count >= 20:
        chunk = min(count // 20, 20)
        out.append(chr(ord("g") + chunk - 1))
        count -= chunk * 20
    if count:
        out.append(chr(ord("G") + count - 1))
    return "".join(out) + char


def compress_rows(rows):
    """Filas hexadecimales con la compresión ASCII de ZPL: ``:`` repite la
    fila anterior, ``,`` completa la fila con ceros, y las corridas de un
    mismo dígito van con un contador."""
    out, previous = [], None
    for row in rows:
        if row == previous:
            out.append(":")
            continue
        previous = row
        stripped = row.rstrip("0")
        if not stripped:
            out.append(",")
            continue
        index, parts = 0, []
        while index < len(stripped):
            char = stripped[index]
            run = 1
            while index + run < len(stripped) and stripped[index + run] == char:
                run += 1
            parts.append(_repeat(run, char) if run > 1 else char)
            index += run
        if len(stripped) < len(row):
            parts.append(",")
        out.append("".join(parts))
    return "".join(out)


def _box_text(shape, palette, context, computed, x0, x1, y0, y1, default_font_pt, dpmm):
    from .shapes_pdf import box_text_lines
    from .text_style import ttf_path, zpl_native_ok, zpl_text_graphic, zpl_text_image
    from .zpl import MM_PER_INCH, POINTS_PER_INCH, _bold_offset, cm_to_dots, escape_field_data, points_to_dots

    text = shape.get("text")
    if not isinstance(text, dict):
        return []
    padding = cm_to_dots(_num(text.get("padding"), DEFAULT_PADDING_MM) / 10, dpmm)
    box_width = (x1 - x0) - 2 * padding
    if box_width <= 0:
        return []
    dots_per_pt = dpmm * MM_PER_INCH / POINTS_PER_INCH
    # Mismo corte en renglones que el PDF (shapes_pdf.box_text_lines).
    style, lines = box_text_lines(shape, context, computed, box_width / dots_per_pt, default_font_pt)
    if not lines:
        return []
    font_dots = max(1, points_to_dots(style["size"], dpmm))
    leading = round(font_dots * style["line_height"])
    block_height = font_dots + leading * (len(lines) - 1)
    valign = text.get("valign", "middle")
    if valign == "top":
        y = y0 + padding
    elif valign == "bottom":
        y = y1 - padding - block_height
    else:
        y = round((y0 + y1) / 2 - block_height / 2)
    align = ALIGN_TO_ZPL.get(text.get("align", "left"), "L")
    color = resolve_color(text.get("color"), palette) or "#000000"
    reverse = thermal_text_is_white(color)

    if not zpl_native_ok(style):
        image = zpl_text_image(
            lines, ttf_path(style["family"], style["bold"], style["italic"]), style["size"] * dots_per_pt,
            box_width, {"L": "left", "C": "center", "R": "right"}[align], style["line_height"],
            style["letter_spacing"] * dots_per_pt,
        )
        return zpl_text_graphic(x0 + padding, y, image, reverse)

    block = f"{len(lines)},{leading - font_dots}" if len(lines) > 1 else "1,0"
    # La impresora corta los renglones con su fuente (^FB), como en los
    # campos: "\\&" (salto de línea de ZPL) choca con el ^FH\\ de los escapes.
    payload = escape_field_data(" ".join(lines))

    def field(offset):
        return [
            f"^FO{x0 + padding + offset},{y + offset}",
            f"^A0N,{font_dots},0",
            f"^FB{box_width},{block},{align},0",
            *(["^FR"] if reverse else []),
            "^FH\\",
            f"^FD{payload}^FS",
        ]

    commands = field(0)
    if style["bold"] and not reverse:
        commands += field(_bold_offset(font_dots))
    return commands
