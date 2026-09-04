"""Backend de dibujo a PNG, con Pillow.

Es el gemelo de ``pdf.py`` y sirve para la vista previa del editor: un PNG se
muestra en un ``<img>`` sin plugins ni visores.

Dos diferencias con el backend PDF:

- **El origen coincide** con el del modelo (arriba a la izquierda, Y hacia
  abajo), así que no hay ninguna inversión de eje. Es el backend fácil.
- **Se trabaja en píxeles**, no en milímetros. La conversión sale de
  ``mm_a_px`` —la misma función que usan ``Plantilla.ancho_px`` y el resto del
  sistema—, así que el lienzo y lo que va encima no pueden quedar corridos
  entre sí.

Para imprimir conviene el PDF: es vectorial, el texto queda seleccionable y no
se pixela al ampliar. El PNG es para mirar en pantalla.
"""

import io

from PIL import Image, ImageDraw, ImageFont

from apps.labels.models import mm_a_px

from . import fuentes
from .primitivas import Imagen, Linea, Rectangulo, Texto
from .resolucion import PT_POR_MM, linea_base_mm


def dibujar(lienzo, dpi=None):
    """Devuelve los bytes de un PNG con el lienzo dibujado.

    ``dpi`` sobreescribe el de la plantilla, para pedir una vista previa
    liviana (150) sin tocar la plantilla ni el PDF de impresión.
    """
    dpi = dpi or lienzo.dpi
    ancho = mm_a_px(lienzo.ancho_mm, dpi)
    alto = mm_a_px(lienzo.alto_mm, dpi)

    imagen = Image.new("RGB", (max(ancho, 1), max(alto, 1)), lienzo.color_fondo)
    lapiz = ImageDraw.Draw(imagen)

    for primitiva in lienzo.primitivas:
        if isinstance(primitiva, Texto):
            _texto(lapiz, primitiva, dpi)
        elif isinstance(primitiva, Imagen):
            _imagen(imagen, primitiva, dpi)
        elif isinstance(primitiva, Linea):
            _linea(lapiz, primitiva, dpi)
        elif isinstance(primitiva, Rectangulo):
            _rectangulo(lapiz, primitiva, dpi)

    buffer = io.BytesIO()
    # dpi en la cabecera del PNG: así un visor que lo respete muestra el
    # tamaño físico correcto en vez de suponer 96 puntos por pulgada.
    imagen.save(buffer, format="PNG", dpi=(dpi, dpi))
    return buffer.getvalue()


def _fuente(tamano_pt, dpi, negrita, cursiva):
    """Carga el TTF al tamaño en píxeles que corresponde a ese DPI."""
    alto_px = max(mm_a_px(tamano_pt / PT_POR_MM, dpi), 1)
    return ImageFont.truetype(fuentes.ruta_ttf(negrita, cursiva), alto_px)


def _texto(lapiz, t, dpi):
    fuente = _fuente(t.tamano_pt, dpi, t.negrita, t.cursiva)
    base_mm = linea_base_mm(t.y_mm, t.alto_mm, t.tamano_pt)

    if t.alineacion == "centro":
        x_mm, anclaje = t.x_mm + t.ancho_mm / 2, "ms"
    elif t.alineacion == "derecha":
        x_mm, anclaje = t.x_mm + t.ancho_mm, "rs"
    else:
        x_mm, anclaje = t.x_mm, "ls"

    # El anclaje termina en "s" (baseline) en los tres casos, así que Pillow
    # sitúa el texto por la misma referencia que reportlab y los dos backends
    # producen la misma ubicación vertical.
    lapiz.text(
        (mm_a_px(x_mm, dpi), mm_a_px(base_mm, dpi)),
        t.texto,
        font=fuente,
        fill=t.color,
        anchor=anclaje,
    )


def _imagen(destino, img, dpi):
    ancho = max(mm_a_px(img.ancho_mm, dpi), 1)
    alto = max(mm_a_px(img.alto_mm, dpi), 1)

    with Image.open(io.BytesIO(img.png)) as origen:
        origen = origen.convert("RGBA")
        # thumbnail respeta la proporción: un QR estirado deja de escanearse.
        origen.thumbnail((ancho, alto), Image.LANCZOS)
        # Centrado dentro de la caja, igual que el anchor="c" del PDF.
        x = mm_a_px(img.x_mm, dpi) + (ancho - origen.width) // 2
        y = mm_a_px(img.y_mm, dpi) + (alto - origen.height) // 2
        destino.paste(origen, (x, y), origen)


def _linea(lapiz, linea, dpi):
    grosor = max(mm_a_px(linea.grosor_mm, dpi), 1)

    if linea.es_horizontal:
        y = mm_a_px(linea.y_mm + linea.alto_mm / 2, dpi)
        extremos = [
            (mm_a_px(linea.x_mm, dpi), y),
            (mm_a_px(linea.x_mm + linea.ancho_mm, dpi), y),
        ]
    else:
        x = mm_a_px(linea.x_mm + linea.ancho_mm / 2, dpi)
        extremos = [
            (x, mm_a_px(linea.y_mm, dpi)),
            (x, mm_a_px(linea.y_mm + linea.alto_mm, dpi)),
        ]

    lapiz.line(extremos, fill=linea.color, width=grosor)


def _rectangulo(lapiz, r, dpi):
    lapiz.rectangle(
        [
            (mm_a_px(r.x_mm, dpi), mm_a_px(r.y_mm, dpi)),
            (mm_a_px(r.x_mm + r.ancho_mm, dpi), mm_a_px(r.y_mm + r.alto_mm, dpi)),
        ],
        outline=r.color,
        width=max(mm_a_px(r.grosor_mm, dpi), 1),
    )
