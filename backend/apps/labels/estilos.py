"""Contrato del JSON ``estilo`` de un elemento de plantilla.

``ElementoPlantilla.estilo`` es un JSONField abierto, cómodo para no migrar por
cada atributo nuevo pero peligroso si cada parte del sistema inventa sus
propias claves. Este módulo fija cuáles son válidas y con qué valores.

Importa porque el campo tiene **dos extremos que hoy no existen todavía**: el
importador de rótulos por foto será el primero en producirlo, y el motor de
impresión el primero en consumirlo. Sin un contrato acordado de antemano, se
escriben por separado y no coinciden.

Las claves están elegidas para traducirse de forma directa a CSS, que es el
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

Un ``estilo`` vacío es válido y significa "todo por defecto"; solo hace falta
guardar las claves que se apartan de ``ESTILO_POR_DEFECTO``.
"""

import re

from django.core.exceptions import ValidationError

ALINEACIONES = ("izquierda", "centro", "derecha", "justificado")

ESTILO_POR_DEFECTO = {
    "tamano_pt": 10,
    "alineacion": "izquierda",
    "negrita": False,
    "cursiva": False,
    "fuente": None,
    "color": "#000000",
    "grosor_mm": 0.3,
}

CLAVES_VALIDAS = frozenset(ESTILO_POR_DEFECTO)

_COLOR_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validar_numero(clave, valor, minimo):
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ValidationError(f"«{clave}» debe ser un número.")
    if valor < minimo:
        raise ValidationError(f"«{clave}» debe ser mayor o igual que {minimo}.")


def _validar_fuente(valor):
    """Valida el código de familia tipográfica.

    Se importa acá adentro y no arriba a propósito: ``render.fuentes`` es
    quien conoce las familias, y el paquete ``render`` importa este módulo
    para resolver los estilos. Un import a nivel de módulo cerraría el
    círculo; adentro de la función no, porque para cuando se llama, ambos
    módulos ya terminaron de cargarse.
    """
    from apps.labels.render.fuentes import CODIGOS

    if valor is None:
        return  # nulo = la familia por defecto
    if not isinstance(valor, str):
        raise ValidationError("«fuente» debe ser texto o nulo.")
    if valor not in CODIGOS:
        raise ValidationError(
            "«fuente» debe ser una de: {}. Se valida contra la lista en vez de "
            "aceptar cualquier nombre porque una familia que el motor no "
            "conoce no falla al guardar: falla al imprimir, saliendo con otra "
            "tipografía sin avisar.".format(", ".join(sorted(CODIGOS)))
        )


def validar_estilo(estilo):
    """Valida un diccionario de estilo contra el contrato.

    Lanza ``ValidationError`` con un mensaje legible ante claves desconocidas
    o valores fuera de rango. No completa los valores por defecto: eso es
    tarea de quien renderiza, vía :func:`estilo_efectivo`.
    """
    if estilo is None:
        return

    if not isinstance(estilo, dict):
        raise ValidationError("El estilo debe ser un objeto.")

    desconocidas = sorted(set(estilo) - CLAVES_VALIDAS)
    if desconocidas:
        raise ValidationError(
            "Claves de estilo desconocidas: {}. Las válidas son: {}.".format(
                ", ".join(desconocidas), ", ".join(sorted(CLAVES_VALIDAS))
            )
        )

    if "tamano_pt" in estilo:
        _validar_numero("tamano_pt", estilo["tamano_pt"], minimo=1)

    if "grosor_mm" in estilo:
        _validar_numero("grosor_mm", estilo["grosor_mm"], minimo=0)

    if "alineacion" in estilo and estilo["alineacion"] not in ALINEACIONES:
        raise ValidationError(
            "«alineacion» debe ser una de: {}.".format(", ".join(ALINEACIONES))
        )

    for clave in ("negrita", "cursiva"):
        if clave in estilo and not isinstance(estilo[clave], bool):
            raise ValidationError(f"«{clave}» debe ser verdadero o falso.")

    if "fuente" in estilo:
        _validar_fuente(estilo["fuente"])

    if "color" in estilo:
        color = estilo["color"]
        if not isinstance(color, str) or not _COLOR_HEX.match(color):
            raise ValidationError("«color» debe tener el formato #rrggbb.")


def estilo_efectivo(estilo):
    """Devuelve el estilo con los valores por defecto completados.

    Lo usa quien renderiza (vista previa o impresión) para no tener que
    preguntarse por cada clave si vino o no.
    """
    return {**ESTILO_POR_DEFECTO, **(estilo or {})}
