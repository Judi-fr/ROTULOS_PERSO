"""Contrato del JSON ``style`` de un elemento de plantilla.

``LayoutElement.style`` es un JSONField abierto, cómodo para no migrar por
cada atributo nuevo pero peligroso si cada parte del sistema inventa sus
propias claves. Este módulo fija cuáles son válidas y con qué valores.

Importa porque el campo tiene **dos extremos que hoy no existen todavía**: el
importador de rótulos por foto será el primero en producirlo, y el motor de
impresión el primero en consumirlo. Sin un contrato acordado de antemano, se
escriben por separado y no coinciden.

Las claves quedan en español a propósito (mismo criterio que
``LabelTemplate.design``: son formato de datos, no identificadores de
código) y están elegidas para traducirse de forma directa a CSS, que es el
camino más probable tanto para la vista previa en el editor como para la
impresión a PDF:

=============  ======================  =============  ============================
clave          valores                 por defecto    equivalente CSS
=============  ======================  =============  ============================
``tamano_pt``  número > 0              ``10``         ``font-size: {v}pt``
``alineacion`` izquierda/centro/       ``izquierda``  ``text-align``
               derecha/justificado
``negrita``    booleano                ``False``      ``font-weight: bold``
``cursiva``    booleano                ``False``      ``font-style: italic``
``fuente``     helvetica/times/        ``None``       ``font-family``
               courier, o ``None``
``color``      ``#rrggbb``             ``#000000``    ``color``
``grosor_mm``  número >= 0             ``0.3``        ``border-width`` (mm)
=============  ======================  =============  ============================

Las cinco primeras aplican a elementos con texto (``variable`` de tipo texto y
``texto_estatico``); ``grosor_mm`` aplica a ``linea`` y ``recuadro``. Guardar
una clave que no corresponde al tipo del elemento no es un error —el motor de
impresión simplemente la ignora—, pero una clave desconocida sí lo es: casi
siempre es un typo que se descubriría recién al imprimir.

Un ``style`` vacío es válido y significa "todo por defecto"; solo hace falta
guardar las claves que se apartan de ``DEFAULT_STYLE``.
"""

import re

from django.core.exceptions import ValidationError

ALIGNMENTS = ("izquierda", "centro", "derecha", "justificado")

DEFAULT_STYLE = {
    "tamano_pt": 10,
    "alineacion": "izquierda",
    "negrita": False,
    "cursiva": False,
    "fuente": None,
    "color": "#000000",
    "grosor_mm": 0.3,
}

VALID_KEYS = frozenset(DEFAULT_STYLE)

_COLOR_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validate_number(key, value, minimum):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"«{key}» debe ser un número.")
    if value < minimum:
        raise ValidationError(f"«{key}» debe ser mayor o igual que {minimum}.")


def _validate_font(value):
    """Valida el código de familia tipográfica.

    Se importa acá adentro y no arriba a propósito: ``render.fonts`` es
    quien conoce las familias, y el paquete ``render`` importa este módulo
    para resolver los estilos. Un import a nivel de módulo cerraría el
    círculo; adentro de la función no, porque para cuando se llama, ambos
    módulos ya terminaron de cargarse.
    """
    from apps.labels.element_layout_render.fonts import FONT_FAMILY_CODES

    if value is None:
        return  # nulo = la familia por defecto
    if not isinstance(value, str):
        raise ValidationError("«fuente» debe ser texto o nulo.")
    if value not in FONT_FAMILY_CODES:
        raise ValidationError(
            "«fuente» debe ser una de: {}. Se valida contra la lista en vez de "
            "aceptar cualquier nombre porque una familia que el motor no "
            "conoce no falla al guardar: falla al imprimir, saliendo con otra "
            "tipografía sin avisar.".format(", ".join(sorted(FONT_FAMILY_CODES)))
        )


def validate_style(style):
    """Valida un diccionario de estilo contra el contrato.

    Lanza ``ValidationError`` con un mensaje legible ante claves desconocidas
    o valores fuera de rango. No completa los valores por defecto: eso es
    tarea de quien renderiza, vía :func:`effective_style`.
    """
    if style is None:
        return

    if not isinstance(style, dict):
        raise ValidationError("El estilo debe ser un objeto.")

    unknown = sorted(set(style) - VALID_KEYS)
    if unknown:
        raise ValidationError(
            "Claves de estilo desconocidas: {}. Las válidas son: {}.".format(
                ", ".join(unknown), ", ".join(sorted(VALID_KEYS))
            )
        )

    if "tamano_pt" in style:
        _validate_number("tamano_pt", style["tamano_pt"], minimum=1)

    if "grosor_mm" in style:
        _validate_number("grosor_mm", style["grosor_mm"], minimum=0)

    if "alineacion" in style and style["alineacion"] not in ALIGNMENTS:
        raise ValidationError(
            "«alineacion» debe ser una de: {}.".format(", ".join(ALIGNMENTS))
        )

    for key in ("negrita", "cursiva"):
        if key in style and not isinstance(style[key], bool):
            raise ValidationError(f"«{key}» debe ser verdadero o falso.")

    if "fuente" in style:
        _validate_font(style["fuente"])

    if "color" in style:
        color = style["color"]
        if not isinstance(color, str) or not _COLOR_HEX.match(color):
            raise ValidationError("«color» debe tener el formato #rrggbb.")


def effective_style(style):
    """Devuelve el estilo con los valores por defecto completados.

    Lo usa quien renderiza (vista previa o impresión) para no tener que
    preguntarse por cada clave si vino o no.
    """
    return {**DEFAULT_STYLE, **(style or {})}
