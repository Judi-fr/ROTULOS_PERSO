"""Resolución de fuentes para los dos backends.

Los backends difieren justo acá y por eso conviene tenerlo aislado:

- **reportlab** trae Helvetica incorporada en el propio formato PDF. No hay
  archivo que buscar ni nada que instalar: funciona igual en tu Windows y en
  un contenedor vacío.
- **Pillow** no trae ninguna fuente vectorial. Si no se le pasa un ``.ttf``
  dibuja con una tipografía de mapa de bits diminuta e inservible, y encima no
  avisa. Hay que salir a buscar un archivo del sistema.

Esa asimetría es la razón de que el PNG pueda fallar en un entorno donde el
PDF anda perfecto — típicamente al pasar de desarrollo a Docker.

``estilo.fuente`` (el ``font-family`` del contrato) **se ignora en esta
etapa**: soportar tipografías arbitrarias implica registrar TTFs por nombre en
reportlab y encontrarlos en el sistema para Pillow, y es una función aparte.
Se usa siempre la fuente por defecto.
"""

import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

# Las cuatro variantes de Helvetica que el estándar PDF garantiza. No hace
# falta empaquetar nada: cualquier lector de PDF las tiene.
_HELVETICA = {
    (False, False): "Helvetica",
    (True, False): "Helvetica-Bold",
    (False, True): "Helvetica-Oblique",
    (True, True): "Helvetica-BoldOblique",
}

# Familias TTF conocidas, en orden de preferencia, para Pillow. Cada entrada
# son las cuatro variantes (normal, negrita, cursiva, negrita+cursiva).
_FAMILIAS_TTF = (
    # Linux / Docker (paquete fonts-dejavu-core)
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
    ),
    # Windows
    (
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/ariali.ttf",
        "C:/Windows/Fonts/arialbi.ttf",
    ),
    # macOS
    (
        "/Library/Fonts/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/Library/Fonts/Arial Italic.ttf",
        "/Library/Fonts/Arial Bold Italic.ttf",
    ),
)


class FuenteNoDisponible(Exception):
    """No hay ninguna fuente TTF utilizable para renderizar a PNG."""


def nombre_pdf(negrita=False, cursiva=False):
    """Nombre de la fuente PDF para una combinación de estilos."""
    return _HELVETICA[(bool(negrita), bool(cursiva))]


def ruta_ttf(negrita=False, cursiva=False):
    """Ruta del ``.ttf`` a usar en Pillow.

    Busca primero lo que indique ``RENDER_FUENTES_TTF`` en settings y después
    las familias conocidas. Si una familia existe pero le falta alguna
    variante, se cae a la normal: preferible un texto sin negrita que un error.
    """
    familias = list(getattr(settings, "RENDER_FUENTES_TTF", ()) or ())
    familias.extend(_FAMILIAS_TTF)

    indice = (1 if negrita else 0) + (2 if cursiva else 0)

    for familia in familias:
        if not familia or not os.path.exists(familia[0]):
            continue
        elegida = familia[indice] if indice < len(familia) else None
        if elegida and os.path.exists(elegida):
            return elegida
        logger.debug("Falta la variante %s de %s; se usa la normal", indice, familia[0])
        return familia[0]

    raise FuenteNoDisponible(
        "No se encontró ninguna fuente TTF para generar el PNG. En Debian/"
        "Ubuntu instalá `fonts-dejavu-core`, o apuntá RENDER_FUENTES_TTF en "
        "settings a las rutas de una familia. El PDF no necesita esto: "
        "reportlab trae Helvetica incorporada."
    )
