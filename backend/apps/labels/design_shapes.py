"""Formas, cajas con contenido, paleta y capas del diseño (System 1).

Dos claves opcionales del JSON ``design`` (fase 1 del editor,
docs/superpowers/specs/2026-10-28-editor-rotulos-galeria-design.md):

- ``palette``: ``{"primary": "#rrggbb", "accent": "#rrggbb"}``, los colores
  del diseño. Un color puede ser ``"primary"``/``"accent"`` y cambiar la
  paleta recolorea todo lo que la usa.
- ``shapes``: lista de formas ``{"id", "kind", "left", "top", "width",
  "height", "fill", "stroke", "stroke_width", "stroke_style", "radius", "z",
  "locked", "text"}``. ``kind`` es ``rect``, ``ellipse`` o ``line`` (una línea
  es horizontal con ``height`` 0 o vertical con ``width`` 0). Una ``rect`` o
  ``ellipse`` con ``text`` es una caja con contenido (una celda): ``text`` =
  ``{"value", "font_size", "bold", "align", "valign", "color", "padding",
  "hide_if_empty"}``.

Posición y tamaño en % del rótulo, como el resto del diseño; ``radius`` y
``padding`` en mm; ``stroke_width`` en pt. Un diseño sin estas claves dibuja
igual que antes. Acá vive lo que comparten el PDF (``shapes_pdf``), el ZPL
(``shapes_zpl``) y la validación: colores, tono en térmica y límites.
"""

import re

from rest_framework import serializers

SHAPE_KINDS = ("rect", "ellipse", "line", "icon")
STROKE_STYLES = ("solid", "dashed", "dotted")
TEXT_ALIGNS = ("left", "center", "right")
VALIGNS = ("top", "middle", "bottom")
PALETTE_KEYS = ("primary", "accent")
MAX_SHAPES = 60
MAX_SHAPE_TEXT_LENGTH = 300
SHAPE_ID_RE = re.compile(r"^[a-z0-9_-]{1,20}$")
HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

RADIUS_MM_MAX = 20
PADDING_MM_MAX = 20
STROKE_WIDTH_PT_MAX = 6
SHAPE_FONT_SIZE_MIN, SHAPE_FONT_SIZE_MAX = 5, 72
DEFAULT_STROKE_WIDTH_PT = 0.6
DEFAULT_PADDING_MM = 1.0

# Térmica: luminancia (0-255) de un color. Más oscuro que BLACK_BELOW sale
# negro, más claro que WHITE_ABOVE no se imprime, en el medio va con trama.
# WHITE_ABOVE es alto a propósito: un gris claro de encabezado de celda (tipo
# OCA, #e5e7eb) tiene que verse como trama suave, no desaparecer.
# Un texto con luminancia >= TEXT_WHITE_FROM sale blanco (invertido).
THERMAL_BLACK_BELOW = 96
THERMAL_WHITE_ABOVE = 245
THERMAL_TEXT_WHITE_FROM = 160

SHAPE_TARGET_PREFIX = "shape:"


def shape_rule_target(shape_id):
    """Cómo una regla apunta a una forma: ``"shape:<id>"``."""
    return f"{SHAPE_TARGET_PREFIX}{shape_id}"


def parse_hex(value):
    """``"#abc"``/``"#aabbcc"`` → ``(r, g, b)``; ValueError si no es un color."""
    if not isinstance(value, str) or not HEX_RE.match(value):
        raise ValueError(f"Color inválido: {value!r}")
    digits = value[1:]
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return tuple(int(digits[i:i + 2], 16) for i in (0, 2, 4))


def resolve_color(value, palette):
    """El color en ``"#rrggbb"`` (minúsculas), resolviendo ``primary``/
    ``accent`` con ``palette``. ``None`` = sin color, también cuando la
    paleta no tiene esa clave o el valor no sirve: el render nunca falla por
    un color."""
    if value in PALETTE_KEYS:
        value = (palette or {}).get(value)
    try:
        r, g, b = parse_hex(value)
    except ValueError:
        return None
    return f"#{r:02x}{g:02x}{b:02x}"


def stroke_color(shape, palette, default=None):
    """El color del borde/línea de ``shape``, o ``None`` si no lleva: sin
    color o con grosor 0 (ReportLab dibujaría la línea más fina posible y el
    ZPL un punto; "0" es "sin borde" en los dos)."""
    width = shape.get("stroke_width")
    if isinstance(width, (int, float)) and not isinstance(width, bool) and width <= 0:
        return None
    return resolve_color(shape.get("stroke"), palette) or default


def luminance(hex_color):
    r, g, b = parse_hex(hex_color)
    return 0.299 * r + 0.587 * g + 0.114 * b


def thermal_tone(hex_color):
    """Cómo sale ``hex_color`` en una térmica: ``("black", 0)``,
    ``("white", 0)`` o ``("gray", level)``, con ``level`` (0-1) la
    proporción de puntos negros de la trama."""
    lum = luminance(hex_color)
    if lum < THERMAL_BLACK_BELOW:
        return ("black", 0)
    if lum > THERMAL_WHITE_ABOVE:
        return ("white", 0)
    span = THERMAL_WHITE_ABOVE - THERMAL_BLACK_BELOW
    return ("gray", round(1 - (lum - THERMAL_BLACK_BELOW) / span, 3))


def thermal_text_is_white(hex_color):
    return luminance(hex_color) >= THERMAL_TEXT_WHITE_FROM


# --- Validación (la llama serializers.validate_design) ------------------------


def _number(value, label, minimum, maximum):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not (minimum <= value <= maximum):
        raise serializers.ValidationError(f"'{label}' debe ser un número entre {minimum} y {maximum}.")


def _color(value, label, palette):
    if value is None:
        return
    if value in PALETTE_KEYS:
        if not (palette or {}).get(value):
            name = "principal" if value == "primary" else "de acento"
            raise serializers.ValidationError(
                f"'{label}' usa el color {name} del diseño, pero el diseño no lo tiene definido."
            )
        return
    if not isinstance(value, str) or not HEX_RE.match(value):
        raise serializers.ValidationError(
            f"'{label}' debe ser un color como #1e3a8a, \"primary\", \"accent\" o vacío."
        )


def validate_palette(value):
    if not isinstance(value, dict):
        raise serializers.ValidationError("'palette' debe ser un objeto.")
    extra = set(value) - set(PALETTE_KEYS)
    if extra:
        raise serializers.ValidationError(f"'palette' no admite: {', '.join(sorted(extra))}.")
    for key, color in value.items():
        if not isinstance(color, str) or not HEX_RE.match(color):
            raise serializers.ValidationError(f"'palette.{key}' debe ser un color como #1e3a8a.")


def _validate_shape_text(text, label, palette):
    if not isinstance(text, dict):
        raise serializers.ValidationError(f"'{label}' debe ser un objeto.")
    allowed = {
        "value", "font_size", "bold", "align", "valign", "color", "padding", "hide_if_empty",
        "font_family", "italic", "uppercase", "letter_spacing", "max_lines", "line_height",
    }
    extra = set(text) - allowed
    if extra:
        raise serializers.ValidationError(f"'{label}' no admite: {', '.join(sorted(extra))}.")
    value = text.get("value", "")
    if not isinstance(value, str) or len(value) > MAX_SHAPE_TEXT_LENGTH:
        raise serializers.ValidationError(
            f"'{label}.value' debe ser un texto de hasta {MAX_SHAPE_TEXT_LENGTH} caracteres."
        )
    if "font_size" in text:
        _number(text["font_size"], f"{label}.font_size", SHAPE_FONT_SIZE_MIN, SHAPE_FONT_SIZE_MAX)
    if "padding" in text:
        _number(text["padding"], f"{label}.padding", 0, PADDING_MM_MAX)
    for flag in ("bold", "hide_if_empty"):
        if flag in text and not isinstance(text[flag], bool):
            raise serializers.ValidationError(f"'{label}.{flag}' debe ser verdadero/falso.")
    if "align" in text and text["align"] not in TEXT_ALIGNS:
        raise serializers.ValidationError(f"'{label}.align' debe ser uno de: {', '.join(TEXT_ALIGNS)}.")
    if "valign" in text and text["valign"] not in VALIGNS:
        raise serializers.ValidationError(f"'{label}.valign' debe ser uno de: {', '.join(VALIGNS)}.")
    _color(text.get("color"), f"{label}.color", palette)
    from .text_style import validate_text_typography

    validate_text_typography(text, label, palette, with_color=False)


def _validate_shape(shape, label, palette):
    if not isinstance(shape, dict):
        raise serializers.ValidationError(f"'{label}' debe ser un objeto.")
    allowed = {
        "id", "kind", "left", "top", "width", "height", "fill", "stroke", "stroke_width",
        "stroke_style", "radius", "z", "locked", "text", "icon",
    }
    extra = set(shape) - allowed
    if extra:
        raise serializers.ValidationError(f"'{label}' no admite: {', '.join(sorted(extra))}.")
    if not isinstance(shape.get("id"), str) or not SHAPE_ID_RE.match(shape["id"]):
        raise serializers.ValidationError(
            f"'{label}.id' es obligatorio: letras minúsculas, números, - o _ (hasta 20)."
        )
    kind = shape.get("kind")
    if kind not in SHAPE_KINDS:
        raise serializers.ValidationError(f"'{label}.kind' debe ser uno de: {', '.join(SHAPE_KINDS)}.")
    for coord in ("left", "top"):
        _number(shape.get(coord), f"{label}.{coord}", 0, 100)
    for size in ("width", "height"):
        if size in shape:
            _number(shape[size], f"{label}.{size}", 0, 100)
    if kind == "line":
        width, height = shape.get("width", 0), shape.get("height", 0)
        if (width > 0) == (height > 0):
            raise serializers.ValidationError(
                f"'{label}': una línea es horizontal (width) o vertical (height), no las dos."
            )
        if "text" in shape:
            raise serializers.ValidationError(f"'{label}': una línea no lleva texto.")
    if kind == "icon":
        # Íconos (fase 2): uno del set fijo de icons.json, color en "fill".
        from .icons import ICON_NAMES

        if shape.get("icon") not in ICON_NAMES:
            raise serializers.ValidationError(
                f"'{label}.icon' debe ser uno de: {', '.join(ICON_NAMES)}."
            )
        if "text" in shape:
            raise serializers.ValidationError(f"'{label}': un ícono no lleva texto.")
    elif "icon" in shape:
        raise serializers.ValidationError(f"'{label}.icon' solo va en una forma de tipo ícono.")
    if "stroke_width" in shape:
        _number(shape["stroke_width"], f"{label}.stroke_width", 0, STROKE_WIDTH_PT_MAX)
    if "radius" in shape:
        _number(shape["radius"], f"{label}.radius", 0, RADIUS_MM_MAX)
    if "z" in shape:
        _number(shape["z"], f"{label}.z", -1000, 1000)
    if "stroke_style" in shape and shape["stroke_style"] not in STROKE_STYLES:
        raise serializers.ValidationError(
            f"'{label}.stroke_style' debe ser uno de: {', '.join(STROKE_STYLES)}."
        )
    if "locked" in shape and not isinstance(shape["locked"], bool):
        raise serializers.ValidationError(f"'{label}.locked' debe ser verdadero/falso.")
    for key in ("fill", "stroke"):
        _color(shape.get(key), f"{label}.{key}", palette)
    if "text" in shape:
        _validate_shape_text(shape["text"], f"{label}.text", palette)


def validate_shapes(value, palette):
    if not isinstance(value, list):
        raise serializers.ValidationError("'shapes' debe ser una lista.")
    if len(value) > MAX_SHAPES:
        raise serializers.ValidationError(f"'shapes' admite como máximo {MAX_SHAPES} formas.")
    seen = set()
    for index, shape in enumerate(value):
        _validate_shape(shape, f"shapes[{index}]", palette)
        if shape["id"] in seen:
            raise serializers.ValidationError(f"'shapes[{index}].id' está repetido: {shape['id']}.")
        seen.add(shape["id"])


def shape_targets(design):
    """Los ``target`` de regla que aportan las formas de ``design``."""
    shapes = design.get("shapes") if isinstance(design, dict) else None
    if not isinstance(shapes, list):
        return set()
    return {
        shape_rule_target(shape["id"])
        for shape in shapes
        if isinstance(shape, dict) and isinstance(shape.get("id"), str)
    }
