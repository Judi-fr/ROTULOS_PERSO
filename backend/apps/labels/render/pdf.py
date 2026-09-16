"""Backend de dibujo a PDF, con reportlab.

Este módulo no decide nada: recibe lienzos ya resueltos y los pinta.

**La única complicación real es el sistema de coordenadas.** reportlab hereda
del estándar PostScript el origen en la esquina **inferior** izquierda, con la
Y creciendo hacia arriba. El modelo de datos —igual que CSS, igual que
cualquier editor gráfico— pone el origen **arriba** a la izquierda con la Y
creciendo hacia abajo.

Sin convertir, un rótulo sale dado vuelta en espejo vertical: lo que iba
arriba aparece abajo. Y como no es un error que rompa nada, se descubre recién
mirando el PDF.

Toda la conversión pasa por :func:`_y`, que además de invertir el eje corre el
punto de referencia al borde inferior de la caja, que es lo que esperan las
primitivas de reportlab.
"""

import io

from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as canvas_pdf

from . import fuentes
from .primitivas import Imagen, Linea, Rectangulo, Texto
from .resolucion import color_rgb, linea_base_mm


def dibujar(lienzos):
    """Devuelve los bytes de un PDF con un lienzo por página.

    Acepta una lista para poder resolver el caso real de un depósito que
    despacha doscientos paquetes: un solo PDF, un rótulo por página, una sola
    orden de impresión.
    """
    buffer = io.BytesIO()
    lienzos = list(lienzos)
    if not lienzos:
        raise ValueError("No hay nada para renderizar.")

    lienzo = lienzos[0]
    c = canvas_pdf.Canvas(
        buffer, pagesize=(lienzo.ancho_mm * mm, lienzo.alto_mm * mm)
    )

    for indice, lienzo in enumerate(lienzos):
        if indice:
            # Cada rótulo puede tener su propio tamaño de papel.
            c.setPageSize((lienzo.ancho_mm * mm, lienzo.alto_mm * mm))
        _dibujar_pagina(c, lienzo)
        c.showPage()

    c.save()
    return buffer.getvalue()


def _dibujar_pagina(c, lienzo):
    _fondo(c, lienzo)

    for primitiva in lienzo.primitivas:
        if isinstance(primitiva, Texto):
            _texto(c, lienzo, primitiva)
        elif isinstance(primitiva, Imagen):
            _imagen(c, lienzo, primitiva)
        elif isinstance(primitiva, Linea):
            _linea(c, lienzo, primitiva)
        elif isinstance(primitiva, Rectangulo):
            _rectangulo(c, lienzo, primitiva)


def _y(lienzo, y_mm, alto_mm=0):
    """Convierte una Y de arriba-abajo (modelo) a abajo-arriba (PDF).

    ``y_mm`` es el borde **superior** de la caja en el modelo; reportlab quiere
    el borde **inferior** contado desde abajo del papel::

        modelo:  y_mm ─────┬──────┐
                           │ caja │ alto_mm
                    ───────┴──────┘
        PDF:     alto_total - y_mm - alto_mm  ← lo que devuelve esta función
    """
    return (lienzo.alto_mm - y_mm - alto_mm) * mm


def _fondo(c, lienzo):
    """Pinta el color de fondo del rótulo, si no es blanco.

    Un PDF es transparente por defecto y sobre papel blanco eso ya es blanco,
    así que el caso normal no necesita ningún rectángulo.
    """
    if (lienzo.color_fondo or "#ffffff").lower() in ("#ffffff", "#fff"):
        return
    c.setFillColorRGB(*color_rgb(lienzo.color_fondo))
    c.rect(0, 0, lienzo.ancho_mm * mm, lienzo.alto_mm * mm, stroke=0, fill=1)


def _texto(c, lienzo, t):
    c.setFont(fuentes.nombre_pdf(t.negrita, t.cursiva, t.fuente), t.tamano_pt)
    c.setFillColorRGB(*color_rgb(t.color))

    base_mm = linea_base_mm(t.y_mm, t.alto_mm, t.tamano_pt)
    y = _y(lienzo, base_mm)

    if t.alineacion == "centro":
        c.drawCentredString((t.x_mm + t.ancho_mm / 2) * mm, y, t.texto)
    elif t.alineacion == "derecha":
        c.drawRightString((t.x_mm + t.ancho_mm) * mm, y, t.texto)
    else:
        # "justificado" cae acá: justificar una sola línea es alinear a la
        # izquierda, y los elementos de un rótulo son de una línea.
        c.drawString(t.x_mm * mm, y, t.texto)


def _imagen(c, lienzo, img):
    c.drawImage(
        ImageReader(io.BytesIO(img.png)),
        img.x_mm * mm,
        _y(lienzo, img.y_mm, img.alto_mm),
        width=img.ancho_mm * mm,
        height=img.alto_mm * mm,
        # Un QR deformado deja de ser escaneable, así que la imagen se ajusta
        # dentro de la caja conservando su proporción en vez de estirarse.
        preserveAspectRatio=True,
        anchor="c",
        mask="auto",
    )


def _linea(c, lienzo, linea):
    c.setStrokeColorRGB(*color_rgb(linea.color))
    c.setLineWidth(linea.grosor_mm * mm)

    if linea.es_horizontal:
        # Se dibuja por el centro vertical de la caja: el grosor crece hacia
        # ambos lados y la línea queda donde el diseñador la puso.
        y = _y(lienzo, linea.y_mm + linea.alto_mm / 2)
        c.line(linea.x_mm * mm, y, (linea.x_mm + linea.ancho_mm) * mm, y)
    else:
        x = (linea.x_mm + linea.ancho_mm / 2) * mm
        c.line(x, _y(lienzo, linea.y_mm), x, _y(lienzo, linea.y_mm + linea.alto_mm))


def _rectangulo(c, lienzo, r):
    c.setStrokeColorRGB(*color_rgb(r.color))
    c.setLineWidth(r.grosor_mm * mm)
    c.rect(
        r.x_mm * mm,
        _y(lienzo, r.y_mm, r.alto_mm),
        r.ancho_mm * mm,
        r.alto_mm * mm,
        stroke=1,
        fill=0,
    )
