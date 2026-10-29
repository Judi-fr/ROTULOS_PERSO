"""Tipografía de los textos del rótulo (fase 2 del editor,
docs/superpowers/specs/2026-10-28-editor-rotulos-galeria-design.md).

Cinco familias. ``sans`` es la Helvetica que el PDF trae de fábrica (la de
siempre: un diseño viejo no cambia); las otras cuatro son ``.ttf`` con
licencia OFL guardadas en ``fonts/`` (bajadas de github.com/google/fonts), con
una copia idéntica en ``frontend/assets/fonts/`` para que el lienzo del editor
use el MISMO archivo (lo verifica ``test_frontend_copies_are_identical``).
Liberation Sans (OFL, métricamente igual a Helvetica) solo se usa para
dibujar la sans como gráfico en ZPL.

Si una familia no tiene una variante (la manuscrita no tiene itálica, la
condensada tampoco), se usa la más cercana: nunca falla por eso.
"""

from functools import lru_cache
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

FONT_FAMILIES = ("sans", "serif", "script", "condensed", "mono")
DEFAULT_FAMILY = "sans"

# Familia → variante → archivo en fonts/. Lo que falta cae a otra variante
# (ver _VARIANT_FALLBACK).
FONT_FILES = {
    "sans": {
        "regular": "LiberationSans-Regular.ttf",
        "bold": "LiberationSans-Bold.ttf",
        "italic": "LiberationSans-Italic.ttf",
        "bolditalic": "LiberationSans-BoldItalic.ttf",
    },
    "serif": {
        "regular": "CrimsonText-Regular.ttf",
        "bold": "CrimsonText-Bold.ttf",
        "italic": "CrimsonText-Italic.ttf",
        "bolditalic": "CrimsonText-BoldItalic.ttf",
    },
    "script": {"regular": "Kalam-Regular.ttf", "bold": "Kalam-Bold.ttf"},
    "condensed": {"regular": "PT_Sans-Narrow-Web-Regular.ttf", "bold": "PT_Sans-Narrow-Web-Bold.ttf"},
    "mono": {
        "regular": "CourierPrime-Regular.ttf",
        "bold": "CourierPrime-Bold.ttf",
        "italic": "CourierPrime-Italic.ttf",
        "bolditalic": "CourierPrime-BoldItalic.ttf",
    },
}

# La sans del PDF es la Helvetica incorporada (sin archivo).
SANS_PDF_NAMES = {
    "regular": "Helvetica",
    "bold": "Helvetica-Bold",
    "italic": "Helvetica-Oblique",
    "bolditalic": "Helvetica-BoldOblique",
}

_VARIANT_FALLBACK = {
    "bolditalic": ("bolditalic", "bold", "italic", "regular"),
    "bold": ("bold", "regular"),
    "italic": ("italic", "regular"),
    "regular": ("regular",),
}

_PDF_PREFIX = {"serif": "RotulosSerif", "script": "RotulosScript", "condensed": "RotulosCondensed", "mono": "RotulosMono"}
_registered = set()


def _variant(bold, italic):
    return ("bold" if bold else "") + ("italic" if italic else "") or "regular"


def _family(family):
    return family if family in FONT_FAMILIES else DEFAULT_FAMILY


def _available_variant(family, bold, italic):
    files = FONT_FILES[family]
    for variant in _VARIANT_FALLBACK[_variant(bold, italic)]:
        if variant in files:
            return variant
    return "regular"


def ttf_path(family, bold=False, italic=False):
    """El ``.ttf`` de esa familia y variante (o la más cercana), para Pillow."""
    family = _family(family)
    return FONTS_DIR / FONT_FILES[family][_available_variant(family, bold, italic)]


def pdf_font_name(family, bold=False, italic=False):
    """El nombre de fuente de ReportLab (registrándola la primera vez)."""
    family = _family(family)
    if family == "sans":
        return SANS_PDF_NAMES[_variant(bold, italic)]
    variant = _available_variant(family, bold, italic)
    name = _PDF_PREFIX[family] + ("" if variant == "regular" else f"-{variant}")
    if name not in _registered:
        pdfmetrics.registerFont(TTFont(name, str(FONTS_DIR / FONT_FILES[family][variant])))
        _registered.add(name)
    return name


# --- Validación (la llaman serializers._validate_text_style y
# design_shapes._validate_shape_text) -----------------------------------------

LETTER_SPACING_MIN, LETTER_SPACING_MAX = -1, 10
MAX_LINES_MIN, MAX_LINES_MAX = 1, 10
LINE_HEIGHT_MIN, LINE_HEIGHT_MAX = 0.8, 3
DEFAULT_LINE_HEIGHT = 1.15


def validate_text_typography(entry, label, palette, with_color=True):
    """Las claves de tipografía de un texto (campo, ``texts[]`` o caja). El
    color de una caja ya lo valida design_shapes: ``with_color=False``."""
    from rest_framework import serializers

    from .design_shapes import _color, _number

    if "font_family" in entry and entry["font_family"] not in FONT_FAMILIES:
        raise serializers.ValidationError(
            f"'{label}.font_family' debe ser una de: {', '.join(FONT_FAMILIES)}."
        )
    for flag in ("italic", "uppercase"):
        if flag in entry and not isinstance(entry[flag], bool):
            raise serializers.ValidationError(f"'{label}.{flag}' debe ser verdadero/falso.")
    if "letter_spacing" in entry:
        _number(entry["letter_spacing"], f"{label}.letter_spacing", LETTER_SPACING_MIN, LETTER_SPACING_MAX)
    if "max_lines" in entry:
        value = entry["max_lines"]
        if not isinstance(value, int) or isinstance(value, bool) or not (MAX_LINES_MIN <= value <= MAX_LINES_MAX):
            raise serializers.ValidationError(
                f"'{label}.max_lines' debe ser un número entero entre {MAX_LINES_MIN} y {MAX_LINES_MAX}."
            )
    if "line_height" in entry:
        _number(entry["line_height"], f"{label}.line_height", LINE_HEIGHT_MIN, LINE_HEIGHT_MAX)
    if with_color:
        _color(entry.get("color"), f"{label}.color", palette)


# --- Medir, cortar y dibujar (PDF; el ZPL usa las mismas medidas) ------------

ELLIPSIS = "…"


def resolve_text_style(entry, default_size):
    """Lo que hace falta para dibujar un texto con su tipografía."""
    entry = entry or {}
    size = float(entry.get("font_size") or default_size)
    bold, italic = bool(entry.get("bold")), bool(entry.get("italic"))
    family = entry.get("font_family") or DEFAULT_FAMILY
    max_lines = entry.get("max_lines")
    line_height = entry.get("line_height")
    return {
        "family": _family(family),
        "bold": bold,
        "italic": italic,
        "font_name": pdf_font_name(family, bold, italic),
        "size": size,
        "uppercase": bool(entry.get("uppercase")),
        "letter_spacing": float(entry.get("letter_spacing") or 0),
        "max_lines": max_lines if isinstance(max_lines, int) and not isinstance(max_lines, bool) and max_lines > 1 else 1,
        "line_height": float(line_height) if isinstance(line_height, (int, float)) and not isinstance(line_height, bool)
        else DEFAULT_LINE_HEIGHT,
    }


def apply_case(text, style):
    return text.upper() if style.get("uppercase") else text


def text_width(text, font_name, size, letter_spacing=0.0):
    """Ancho en pt, con el espaciado entre letras (entre caracteres, no
    después del último)."""
    width = pdfmetrics.stringWidth(text, font_name, size)
    if letter_spacing and len(text) > 1:
        width += letter_spacing * (len(text) - 1)
    return width


def fit_with_ellipsis(text, font_name, size, max_width, letter_spacing=0.0):
    """``text`` entero si entra; si no, cortado con "…"."""
    if text_width(text, font_name, size, letter_spacing) <= max_width:
        return text
    while text and text_width(text + ELLIPSIS, font_name, size, letter_spacing) > max_width:
        text = text[:-1]
    return (text.rstrip() + ELLIPSIS) if text else ""


def wrap_lines(text, font_name, size, max_width, max_lines, letter_spacing=0.0):
    """Corta ``text`` en hasta ``max_lines`` renglones de ``max_width`` pt:
    por palabras, y una palabra que no entra sola se corta por caracteres. Si
    sobra texto, el último renglón termina en "…"."""
    if max_width <= 0:
        return []
    words = text.split()
    lines, current = [], ""
    while words and len(lines) < max_lines:
        word = words[0]
        candidate = f"{current} {word}" if current else word
        if text_width(candidate, font_name, size, letter_spacing) <= max_width:
            current = candidate
            words.pop(0)
        elif current:
            lines.append(current)
            current = ""
        else:
            # Una palabra sola que no entra: se corta donde llegue.
            cut = len(word)
            while cut > 1 and text_width(word[:cut], font_name, size, letter_spacing) > max_width:
                cut -= 1
            lines.append(word[:cut])
            words[0] = word[cut:]
    if current and len(lines) < max_lines:
        lines.append(current)
        current = ""
    leftover = ([current] if current else []) + words
    if leftover and lines:
        lines[-1] = fit_with_ellipsis(
            lines[-1] + " " + " ".join(leftover), font_name, size, max_width, letter_spacing
        )
    return lines


def draw_text_line(pdf, x, y, text, font_name, size, letter_spacing=0.0):
    """Un renglón que empieza en ``x`` (línea base ``y``). Sin espaciado es
    el ``drawString`` de siempre (el PDF de un diseño viejo no cambia)."""
    if not letter_spacing:
        pdf.setFont(font_name, size)
        pdf.drawString(x, y, text)
        return
    obj = pdf.beginText(x, y)
    obj.setFont(font_name, size)
    obj.setCharSpace(letter_spacing)
    obj.textOut(text)
    pdf.drawText(obj)


# --- ZPL: el texto como gráfico ----------------------------------------------
# La Zebra solo trae su fuente 0 (una sans condensada). Cualquier otra
# tipografía, la itálica o el espaciado se dibujan con Pillow y la MISMA .ttf
# del PDF y van como ^GF: sale como en el PDF, no como una aproximación.


def zpl_native_ok(style):
    """La sans sin itálica ni espaciado sigue con la fuente de la impresora."""
    return style["family"] == "sans" and not style["italic"] and not style["letter_spacing"]


def _pillow_font(ttf, size_dots):
    return _load_pillow_font(str(ttf), max(1, int(round(size_dots))))


@lru_cache(maxsize=64)
def _load_pillow_font(ttf, size):
    from PIL import ImageFont

    return ImageFont.truetype(ttf, size)


def _line_length(font, text, spacing_dots):
    return font.getlength(text) + (spacing_dots * (len(text) - 1) if len(text) > 1 else 0)


def zpl_text_image(lines, ttf, size_dots, box_width_dots, align, line_height, letter_spacing_dots,
                   prefix=None):
    """Los renglones dibujados a 1 bit (1 = tinta). La línea base del primero
    queda a ``size_dots`` del borde de arriba, como el PDF (que baja una
    línea desde el ``top`` del campo). ``prefix`` = ``(texto, ttf)``: la
    etiqueta fija delante del primer renglón, con su propia fuente."""
    from PIL import Image, ImageDraw

    font = _pillow_font(ttf, size_dots)
    prefix_font = _pillow_font(prefix[1], size_dots) if prefix else None
    prefix_width = _line_length(prefix_font, prefix[0], letter_spacing_dots) if prefix else 0
    leading = size_dots * line_height
    descent = font.getmetrics()[1]
    height = int(round(size_dots + leading * (len(lines) - 1) + descent + 1))
    width = max(1, int(box_width_dots))
    image = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(image)

    def write(x, y, text, use_font):
        if not letter_spacing_dots:
            draw.text((x, y), text, font=use_font, fill=1, anchor="ls")
            return
        for char in text:
            draw.text((x, y), char, font=use_font, fill=1, anchor="ls")
            x += use_font.getlength(char) + letter_spacing_dots

    for index, line in enumerate(lines):
        y = size_dots + index * leading
        line_width = prefix_width + _line_length(font, line, letter_spacing_dots)
        start = {"center": (width - line_width) / 2, "right": width - line_width}.get(align, 0)
        if index == 0 and prefix:
            write(start, y, prefix[0], prefix_font)
        write(start + prefix_width, y, line, font)
    return image


def zpl_text_graphic(x_dots, y_dots, image, reverse):
    """``^FO`` + ``^GF`` (con ``^FR`` si va en blanco sobre negro)."""
    from .shapes_zpl import _rows, compress_rows

    rows, bytes_per_row = _rows(image)
    total = bytes_per_row * image.size[1]
    return [
        f"^FO{x_dots},{y_dots}",
        *(["^FR"] if reverse else []),
        f"^GFA,{total},{total},{bytes_per_row},{compress_rows(rows)}^FS",
    ]
