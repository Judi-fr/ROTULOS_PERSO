"""Dibujo en PDF de las formas y cajas con contenido del diseño
(``design["shapes"]``, ver design_shapes.py). Lo llama
``label_rendering.draw_label_page`` después de las decoraciones: las formas
son la capa de fondo, debajo del logo, los códigos y los textos de siempre.
Los números (grosor por defecto, punteados, márgenes) son los mismos que usan
el ZPL (shapes_zpl.py) y el lienzo del editor (label_editor_shapes.js).
"""

import logging

from .design_shapes import DEFAULT_PADDING_MM, DEFAULT_STROKE_WIDTH_PT, parse_hex, resolve_color, stroke_color

logger = logging.getLogger(__name__)

# Punteados (pt): mismos valores en el editor y en ZPL.
DASH_PATTERNS = {"dashed": (4, 3), "dotted": (1, 2)}
# Dónde cae la línea base respecto del tamaño de letra, para centrar un texto
# en su caja: la altura de las mayúsculas de Helvetica es ~0,7 del tamaño.
BASELINE_MIDDLE_FACTOR = 0.35
BASELINE_TOP_FACTOR = 0.8
BASELINE_BOTTOM_FACTOR = 0.2


def ordered_shapes(design):
    """Las formas en el orden en que se dibujan: ``z`` ascendente y, a igual
    ``z``, el orden de la lista. También lo usa el ZPL."""
    shapes = design.get("shapes") if isinstance(design, dict) else None
    if not isinstance(shapes, list):
        return []
    valid = [(index, shape) for index, shape in enumerate(shapes) if isinstance(shape, dict)]
    valid.sort(key=lambda item: (_number(item[1].get("z"), 0), item[0]))
    return [shape for _, shape in valid]


def _number(value, default):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def shape_box_pct(shape):
    """``(left, top, right, bottom)`` en % del rótulo, recortado a 0-100."""
    left = _number(shape.get("left"), 0)
    top = _number(shape.get("top"), 0)
    width = max(0.0, _number(shape.get("width"), 0))
    height = max(0.0, _number(shape.get("height"), 0))
    return (
        min(max(left, 0), 100),
        min(max(top, 0), 100),
        min(max(left + width, 0), 100),
        min(max(top + height, 0), 100),
    )


def shape_is_hidden(shape, context, computed):
    """Una caja con ``text.hide_if_empty`` cuyos marcadores quedan todos
    vacíos no se dibuja (ni la forma)."""
    from .label_rendering import _all_placeholders_empty

    text = shape.get("text")
    return bool(
        isinstance(text, dict)
        and text.get("hide_if_empty")
        and _all_placeholders_empty(text.get("value", ""), context, computed)
    )


def _rgb(hex_color):
    return tuple(channel / 255 for channel in parse_hex(hex_color))


def draw_shapes(pdf, design, context, computed, width_cm, height_cm, default_font_size,
                logo_file=None, warnings=None):
    from .label_rendering import CM_TO_POINTS

    palette = design.get("palette") if isinstance(design.get("palette"), dict) else None
    width_pt, height_pt = width_cm * CM_TO_POINTS, height_cm * CM_TO_POINTS
    mm_to_pt = CM_TO_POINTS / 10
    for shape in ordered_shapes(design):
        if shape.get("kind") not in ("rect", "ellipse", "line", "icon", "image") or shape_is_hidden(shape, context, computed):
            continue
        left, top, right, bottom = shape_box_pct(shape)
        x0, x1 = left / 100 * width_pt, right / 100 * width_pt
        y_top, y_bottom = height_pt * (1 - top / 100), height_pt * (1 - bottom / 100)
        pdf.saveState()
        if shape["kind"] == "image":
            _draw_image(pdf, shape, palette, x0, x1, y_top, y_bottom, mm_to_pt, logo_file, warnings)
        elif shape["kind"] == "icon":
            _draw_icon(pdf, shape, palette, x0, x1, y_top, y_bottom)
        elif shape["kind"] == "line":
            _draw_line(pdf, shape, palette, x0, x1, y_top, y_bottom)
        else:
            _draw_area(pdf, shape, palette, x0, x1, y_top, y_bottom, mm_to_pt)
            _draw_box_text(pdf, shape, palette, context, computed, x0, x1, y_top, y_bottom, mm_to_pt,
                           default_font_size)
        pdf.restoreState()


def _draw_icon(pdf, shape, palette, x0, x1, y_top, y_bottom):
    from .icons import draw_icon_pdf, icon_box

    color = resolve_color(shape.get("fill"), palette) or "#000000"
    x, top_from_top, side = icon_box(x0, -y_top, x1 - x0, y_top - y_bottom)
    if side > 0:
        draw_icon_pdf(pdf, shape.get("icon"), x, -top_from_top, side, _rgb(color))


IMAGE_DPI = 300
IMAGE_MAX_SIDE_PX = 2400


def _draw_image(pdf, shape, palette, x0, x1, y_top, y_bottom, mm_to_pt, logo_file, warnings):
    from reportlab.lib.utils import ImageReader

    from .icons import icon_box
    from .label_images import prepare, resolve_source

    width, height = x1 - x0, y_top - y_bottom
    if width <= 0 or height <= 0:
        return
    circle = bool(shape.get("circle"))
    radius_pt = 0 if circle else min(_number(shape.get("radius"), 0) * mm_to_pt, width / 2, height / 2)
    scale = min(IMAGE_DPI / 72, IMAGE_MAX_SIDE_PX / max(width, height))
    box_w, box_h = max(1, round(width * scale)), max(1, round(height * scale))
    source = resolve_source(shape, logo_file)
    image = None
    if source is not None:
        try:
            image = prepare(source, box_w, box_h, shape.get("fit", "contain"), circle, radius_pt * scale)
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
        return
    pdf.drawImage(ImageReader(image), x0, y_bottom, width, height, mask="auto")
    stroke = stroke_color(shape, palette)
    if not stroke:
        return
    _set_stroke(pdf, shape, stroke)
    if circle:
        cx, cy, side = icon_box(x0, -y_top, width, height)
        pdf.circle(cx + side / 2, -cy - side / 2, side / 2, stroke=1, fill=0)
    elif radius_pt > 0:
        pdf.roundRect(x0, y_bottom, width, height, radius_pt, stroke=1, fill=0)
    else:
        pdf.rect(x0, y_bottom, width, height, stroke=1, fill=0)


def _set_stroke(pdf, shape, color):
    pdf.setStrokeColorRGB(*_rgb(color))
    pdf.setLineWidth(_number(shape.get("stroke_width"), DEFAULT_STROKE_WIDTH_PT))
    pattern = DASH_PATTERNS.get(shape.get("stroke_style"))
    pdf.setDash(*pattern) if pattern else pdf.setDash()


def _draw_line(pdf, shape, palette, x0, x1, y_top, y_bottom):
    color = stroke_color(shape, palette, default="#000000")
    if not color:
        return
    _set_stroke(pdf, shape, color)
    if _number(shape.get("height"), 0) > 0:  # vertical
        pdf.line(x0, y_top, x0, y_bottom)
    else:
        pdf.line(x0, y_top, x1, y_top)


def _draw_area(pdf, shape, palette, x0, x1, y_top, y_bottom, mm_to_pt):
    fill = resolve_color(shape.get("fill"), palette)
    stroke = stroke_color(shape, palette)
    width, height = x1 - x0, y_top - y_bottom
    if (not fill and not stroke) or width <= 0 or height <= 0:
        return
    if fill:
        pdf.setFillColorRGB(*_rgb(fill))
    if stroke:
        _set_stroke(pdf, shape, stroke)
    flags = {"stroke": 1 if stroke else 0, "fill": 1 if fill else 0}
    if shape["kind"] == "ellipse":
        pdf.ellipse(x0, y_bottom, x1, y_top, **flags)
        return
    radius = min(_number(shape.get("radius"), 0) * mm_to_pt, width / 2, height / 2)
    if radius > 0:
        pdf.roundRect(x0, y_bottom, width, height, radius, **flags)
    else:
        pdf.rect(x0, y_bottom, width, height, **flags)


def box_text_lines(shape, context, computed, box_width_pt, default_font_size):
    """``(style, lines)`` del texto de una caja: resuelto, en mayúsculas si
    va, y cortado en sus renglones (``text_style``). También lo usa el ZPL."""
    from .label_rendering import _replace_placeholders
    from .text_style import apply_case, fit_with_ellipsis, resolve_text_style, wrap_lines

    text = shape.get("text")
    if not isinstance(text, dict):
        return None, []
    style = resolve_text_style(text, default_font_size)
    value = apply_case(_replace_placeholders(text.get("value", ""), context, computed).strip(), style)
    if not value or box_width_pt <= 0:
        return style, []
    if style["max_lines"] > 1:
        lines = wrap_lines(value, style["font_name"], style["size"], box_width_pt, style["max_lines"],
                           style["letter_spacing"])
    else:
        lines = [fit_with_ellipsis(value, style["font_name"], style["size"], box_width_pt, style["letter_spacing"])]
    return style, [line for line in lines if line]


def _draw_box_text(pdf, shape, palette, context, computed, x0, x1, y_top, y_bottom, mm_to_pt, default_font_size):
    from .text_style import draw_text_line, text_width

    text = shape.get("text")
    if not isinstance(text, dict):
        return
    padding = _number(text.get("padding"), DEFAULT_PADDING_MM) * mm_to_pt
    style, lines = box_text_lines(shape, context, computed, (x1 - x0) - 2 * padding, default_font_size)
    if not lines:
        return
    size, spacing = style["size"], style["letter_spacing"]
    leading = size * style["line_height"]
    extra = leading * (len(lines) - 1)
    valign = text.get("valign", "middle")
    if valign == "top":
        baseline = y_top - padding - size * BASELINE_TOP_FACTOR
    elif valign == "bottom":
        baseline = y_bottom + padding + size * BASELINE_BOTTOM_FACTOR + extra
    else:
        baseline = (y_top + y_bottom) / 2 - size * BASELINE_MIDDLE_FACTOR + extra / 2
    color = resolve_color(text.get("color"), palette) or "#000000"
    pdf.setFillColorRGB(*_rgb(color))
    align = text.get("align", "left")
    for index, line in enumerate(lines):
        y = baseline - index * leading
        if spacing:
            width = text_width(line, style["font_name"], size, spacing)
            x = {"center": (x0 + x1 - width) / 2, "right": x1 - padding - width}.get(align, x0 + padding)
            draw_text_line(pdf, x, y, line, style["font_name"], size, spacing)
            continue
        pdf.setFont(style["font_name"], size)
        if align == "center":
            pdf.drawCentredString((x0 + x1) / 2, y, line)
        elif align == "right":
            pdf.drawRightString(x1 - padding, y, line)
        else:
            pdf.drawString(x0 + padding, y, line)
