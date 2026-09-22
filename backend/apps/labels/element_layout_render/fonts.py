"""Resolución de fuentes para los dos backends.

Los backends difieren justo acá y por eso conviene tenerlo aislado:

- **reportlab** trae catorce fuentes incorporadas en el propio formato PDF
  (las "standard 14"). No hay archivo que buscar ni nada que instalar:
  funcionan igual en tu Windows y en un contenedor vacío.
- **Pillow** no trae ninguna fuente vectorial. Si no se le pasa un ``.ttf``
  dibuja con una tipografía de mapa de bits diminuta e inservible, y encima no
  avisa. Hay que salir a buscar un archivo del sistema.

Esa asimetría es la razón de que el PNG pueda fallar en un entorno donde el
PDF anda perfecto — típicamente al pasar de desarrollo a Docker.

**Por qué estas tres familias y no otras.** Cada una de :data:`FONT_FAMILIES`
está elegida para cumplir dos condiciones a la vez: que exista dentro del PDF
(así imprimir nunca depende de lo que haya instalado en el servidor) y que
tenga un equivalente casi seguro en Windows, Linux y macOS (así la vista
previa en PNG también sale). Agregar una tipografía de marca es posible, pero
deja de ser gratis: hay que empaquetar el ``.ttf``, registrarlo en reportlab y
sumarlo a la tabla. Mientras tanto, sans / serif / monoespaciada cubren lo que
un rótulo necesita — y la monoespaciada no es un capricho: alinea los números
de seguimiento en columna.

``style.fuente`` guarda el **código** de la familia (``"helvetica"``,
``"times"``, ``"courier"``), no un nombre de archivo ni un ``font-family``
de CSS. Vacío o desconocido significa la familia por defecto.
"""

import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

# Las cuatro variantes van siempre en este orden. El índice se calcula como
# bold + 2*italic, así que no se puede reordenar la tupla sin romper todo.
NORMAL, BOLD, ITALIC, BOLD_ITALIC = 0, 1, 2, 3

DEFAULT_FONT_FAMILY = "helvetica"

FONT_FAMILIES = {
    "helvetica": {
        "label": "Helvetica / Arial",
        "category": "sans serif",
        # Nombres que reportlab entiende sin registrar nada (standard 14).
        "pdf": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique",
                "Helvetica-BoldOblique"),
        # Candidatos para Pillow, en orden de preferencia. El primero de cada
        # tupla es el que decide si la familia está disponible.
        "ttf": (
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"),
            ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf",
             "C:/Windows/Fonts/ariali.ttf", "C:/Windows/Fonts/arialbi.ttf"),
            ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf",
             "/Library/Fonts/Arial Italic.ttf",
             "/Library/Fonts/Arial Bold Italic.ttf"),
        ),
    },
    "times": {
        "label": "Times / Times New Roman",
        "category": "serif",
        "pdf": ("Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"),
        "ttf": (
            ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSerif-BoldItalic.ttf"),
            ("C:/Windows/Fonts/times.ttf", "C:/Windows/Fonts/timesbd.ttf",
             "C:/Windows/Fonts/timesi.ttf", "C:/Windows/Fonts/timesbi.ttf"),
            ("/Library/Fonts/Times New Roman.ttf",
             "/Library/Fonts/Times New Roman Bold.ttf",
             "/Library/Fonts/Times New Roman Italic.ttf",
             "/Library/Fonts/Times New Roman Bold Italic.ttf"),
        ),
    },
    "courier": {
        "label": "Courier (monoespaciada)",
        "category": "monoespaciada",
        "pdf": ("Courier", "Courier-Bold", "Courier-Oblique",
                "Courier-BoldOblique"),
        "ttf": (
            ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Oblique.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-BoldOblique.ttf"),
            ("C:/Windows/Fonts/cour.ttf", "C:/Windows/Fonts/courbd.ttf",
             "C:/Windows/Fonts/couri.ttf", "C:/Windows/Fonts/courbi.ttf"),
            ("/Library/Fonts/Courier New.ttf",
             "/Library/Fonts/Courier New Bold.ttf",
             "/Library/Fonts/Courier New Italic.ttf",
             "/Library/Fonts/Courier New Bold Italic.ttf"),
        ),
    },
}

FONT_FAMILY_CODES = tuple(FONT_FAMILIES)


class FontNotAvailable(Exception):
    """No hay ninguna fuente TTF utilizable para renderizar a PNG."""


def normalize_family(family):
    """Devuelve un código de familia válido.

    Una familia desconocida no rompe el render: se cae a la por defecto y se
    deja constancia en el log. Un rótulo que sale con otra tipografía es un
    problema menor que un rótulo que no sale.
    """
    if family in FONT_FAMILIES:
        return family
    if family:
        logger.info("Familia de fuente desconocida %r; se usa la por defecto", family)
    return DEFAULT_FONT_FAMILY


def _variant_index(bold, italic):
    return (1 if bold else 0) + (2 if italic else 0)


def pdf_font_name(bold=False, italic=False, family=None):
    """Nombre de la fuente PDF para una familia y una combinación de estilos.

    Las tres familias son de las "standard 14", así que este nombre siempre lo
    entiende reportlab sin registrar ningún archivo. También lo usa
    ``resolution.text_width_mm`` para medir: medir con la fuente que se va a
    imprimir es lo que hace que el truncado sea correcto.
    """
    return FONT_FAMILIES[normalize_family(family)]["pdf"][_variant_index(bold, italic)]


def _candidates(code):
    """Tuplas de rutas a probar para una familia, en orden de preferencia.

    ``RENDER_FONTS_TTF`` en settings permite anteponer rutas propias. Es un
    diccionario ``{codigo: (normal, negrita, cursiva, negrita_cursiva)}``,
    pensado para un contenedor que empaqueta sus propias tipografías.
    """
    custom = (getattr(settings, "RENDER_FONTS_TTF", None) or {}).get(code)
    families = [custom] if custom else []
    families.extend(FONT_FAMILIES[code]["ttf"])
    return families


def _find_ttf(code, index):
    """Ruta del ``.ttf`` de una familia concreta, o ``None`` si no está.

    Si una familia existe pero le falta alguna variante, se cae a la normal:
    preferible un texto sin negrita que un error.
    """
    for family in _candidates(code):
        if not family or not os.path.exists(family[0]):
            continue
        chosen = family[index] if index < len(family) else None
        if chosen and os.path.exists(chosen):
            return chosen
        logger.debug("Falta la variante %s de %s; se usa la normal", index, family[0])
        return family[0]
    return None


def ttf_path(bold=False, italic=False, family=None):
    """Ruta del ``.ttf`` a usar en Pillow.

    Si la familia pedida no está instalada se prueba con las otras. La
    sustitución solo afecta al PNG —el PDF nunca la necesita, porque sus
    fuentes viajan dentro del formato—, y el PNG es la vista de pantalla: que
    la previsualización salga con otra tipografía es mejor que que no salga.
    Quien elige en el editor ya ve cuáles están disponibles, vía
    :func:`available_font_families`.
    """
    code = normalize_family(family)
    index = _variant_index(bold, italic)

    path = _find_ttf(code, index)
    if path:
        return path

    for alternative in FONT_FAMILY_CODES:
        if alternative == code:
            continue
        path = _find_ttf(alternative, index)
        if path:
            logger.info(
                "La familia %r no está instalada; el PNG se dibuja con %r",
                code, alternative,
            )
            return path

    raise FontNotAvailable(
        "No se encontró ninguna fuente TTF para generar el PNG. En Debian/"
        "Ubuntu instalá `fonts-dejavu-core`, o apuntá RENDER_FONTS_TTF en "
        "settings a las rutas de las familias. El PDF no necesita esto: "
        "reportlab trae sus fuentes incorporadas."
    )


def available_font_families():
    """Catálogo de familias para el editor.

    ``png_available`` dice si esta máquina tiene el ``.ttf`` de la familia.
    Se informa en lugar de esconder las que faltan porque el PDF —que es lo
    que se imprime— funciona igual: lo único que se resiente es la vista
    previa en pantalla.
    """
    return [
        {
            "code": code,
            "label": data["label"],
            "category": data["category"],
            "is_default": code == DEFAULT_FONT_FAMILY,
            "png_available": _find_ttf(code, NORMAL) is not None,
        }
        for code, data in FONT_FAMILIES.items()
    ]
