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

**Por qué estas tres familias y no otras.** Cada una de :data:`FAMILIAS` está
elegida para cumplir dos condiciones a la vez: que exista dentro del PDF (así
imprimir nunca depende de lo que haya instalado en el servidor) y que tenga un
equivalente casi seguro en Windows, Linux y macOS (así la vista previa en PNG
también sale). Agregar una tipografía de marca es posible, pero deja de ser
gratis: hay que empaquetar el ``.ttf``, registrarlo en reportlab y sumarlo a
la tabla. Mientras tanto, sans / serif / monoespaciada cubren lo que un rótulo
necesita — y la monoespaciada no es un capricho: alinea los números de
seguimiento en columna.

``estilo.fuente`` guarda el **código** de la familia (``"helvetica"``,
``"times"``, ``"courier"``), no un nombre de archivo ni un ``font-family``
de CSS. Vacío o desconocido significa la familia por defecto.
"""

import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

# Las cuatro variantes van siempre en este orden. El índice se calcula como
# negrita + 2*cursiva, así que no se puede reordenar la tupla sin romper todo.
NORMAL, NEGRITA, CURSIVA, NEGRITA_CURSIVA = 0, 1, 2, 3

FAMILIA_POR_DEFECTO = "helvetica"

FAMILIAS = {
    "helvetica": {
        "etiqueta": "Helvetica / Arial",
        "clase": "sans serif",
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
        "etiqueta": "Times / Times New Roman",
        "clase": "serif",
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
        "etiqueta": "Courier (monoespaciada)",
        "clase": "monoespaciada",
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

CODIGOS = tuple(FAMILIAS)


class FuenteNoDisponible(Exception):
    """No hay ninguna fuente TTF utilizable para renderizar a PNG."""


def normalizar(familia):
    """Devuelve un código de familia válido.

    Una familia desconocida no rompe el render: se cae a la por defecto y se
    deja constancia en el log. Un rótulo que sale con otra tipografía es un
    problema menor que un rótulo que no sale.
    """
    if familia in FAMILIAS:
        return familia
    if familia:
        logger.info("Familia de fuente desconocida %r; se usa la por defecto", familia)
    return FAMILIA_POR_DEFECTO


def _indice(negrita, cursiva):
    return (1 if negrita else 0) + (2 if cursiva else 0)


def nombre_pdf(negrita=False, cursiva=False, familia=None):
    """Nombre de la fuente PDF para una familia y una combinación de estilos.

    Las tres familias son de las "standard 14", así que este nombre siempre lo
    entiende reportlab sin registrar ningún archivo. También lo usa
    ``resolucion.ancho_texto_mm`` para medir: medir con la fuente que se va a
    imprimir es lo que hace que el truncado sea correcto.
    """
    return FAMILIAS[normalizar(familia)]["pdf"][_indice(negrita, cursiva)]


def _candidatas(codigo):
    """Tuplas de rutas a probar para una familia, en orden de preferencia.

    ``RENDER_FUENTES_TTF`` en settings permite anteponer rutas propias. Es un
    diccionario ``{codigo: (normal, negrita, cursiva, negrita_cursiva)}``,
    pensado para un contenedor que empaqueta sus propias tipografías.
    """
    propias = (getattr(settings, "RENDER_FUENTES_TTF", None) or {}).get(codigo)
    familias = [propias] if propias else []
    familias.extend(FAMILIAS[codigo]["ttf"])
    return familias


def _buscar_ttf(codigo, indice):
    """Ruta del ``.ttf`` de una familia concreta, o ``None`` si no está.

    Si una familia existe pero le falta alguna variante, se cae a la normal:
    preferible un texto sin negrita que un error.
    """
    for familia in _candidatas(codigo):
        if not familia or not os.path.exists(familia[0]):
            continue
        elegida = familia[indice] if indice < len(familia) else None
        if elegida and os.path.exists(elegida):
            return elegida
        logger.debug("Falta la variante %s de %s; se usa la normal", indice, familia[0])
        return familia[0]
    return None


def ruta_ttf(negrita=False, cursiva=False, familia=None):
    """Ruta del ``.ttf`` a usar en Pillow.

    Si la familia pedida no está instalada se prueba con las otras. La
    sustitución solo afecta al PNG —el PDF nunca la necesita, porque sus
    fuentes viajan dentro del formato—, y el PNG es la vista de pantalla: que
    la previsualización salga con otra tipografía es mejor que que no salga.
    Quien elige en el editor ya ve cuáles están disponibles, vía
    :func:`familias_disponibles`.
    """
    codigo = normalizar(familia)
    indice = _indice(negrita, cursiva)

    ruta = _buscar_ttf(codigo, indice)
    if ruta:
        return ruta

    for alternativa in CODIGOS:
        if alternativa == codigo:
            continue
        ruta = _buscar_ttf(alternativa, indice)
        if ruta:
            logger.info(
                "La familia %r no está instalada; el PNG se dibuja con %r",
                codigo, alternativa,
            )
            return ruta

    raise FuenteNoDisponible(
        "No se encontró ninguna fuente TTF para generar el PNG. En Debian/"
        "Ubuntu instalá `fonts-dejavu-core`, o apuntá RENDER_FUENTES_TTF en "
        "settings a las rutas de las familias. El PDF no necesita esto: "
        "reportlab trae sus fuentes incorporadas."
    )


def familias_disponibles():
    """Catálogo de familias para el editor.

    ``png_disponible`` dice si esta máquina tiene el ``.ttf`` de la familia.
    Se informa en lugar de esconder las que faltan porque el PDF —que es lo
    que se imprime— funciona igual: lo único que se resiente es la vista
    previa en pantalla.
    """
    return [
        {
            "codigo": codigo,
            "etiqueta": datos["etiqueta"],
            "clase": datos["clase"],
            "por_defecto": codigo == FAMILIA_POR_DEFECTO,
            "png_disponible": _buscar_ttf(codigo, NORMAL) is not None,
        }
        for codigo, datos in FAMILIAS.items()
    ]
