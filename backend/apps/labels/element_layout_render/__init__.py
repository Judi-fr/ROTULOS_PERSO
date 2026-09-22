"""Motor de renderizado de rótulos (ElementLayout).

Convierte una plantilla más los datos de un envío en un archivo imprimible.

    from apps.labels.element_layout_render import render_element_layout_pdf, render_element_layout_png

    pdf, report = render_element_layout_pdf(layout, {"destinatario": "María Gómez"},
                                            user=request.user)

El motor está partido en dos etapas, y esa es la decisión que conviene tener
presente antes de tocarlo:

1. ``resolution.py`` toma **todas** las decisiones —qué valor va en cada
   elemento, qué pasa si falta, cómo se genera un QR, qué hacer con un texto
   que no entra— y produce una lista de primitivas en milímetros.
2. ``pdf.py`` y ``png.py`` solo pintan esas primitivas.

Así la lógica existe una sola vez aunque haya dos formatos de salida, y se
puede testear sin generar archivos ni comparar imágenes: se afirma sobre la
lista de primitivas.

Si agregás un formato nuevo (SVG, ZPL para impresoras térmicas), escribís otro
backend de la etapa 2 y no tocás nada de la 1.
"""

from .primitives import Canvas, Image, Line, Rectangle, Text
from .resolution import resolve

__all__ = [
    "render_element_layout_pdf",
    "render_element_layout_png",
    "resolve",
    "Canvas",
    "Text",
    "Image",
    "Line",
    "Rectangle",
]


def _build_report(canvases):
    """Junta lo que hay que reportarle a quien pidió el render.

    Se acumula por lote y sin repetir: en un PDF de doscientos rótulos el
    problema suele ser el mismo doscientas veces, y listarlo doscientas veces
    no agrega nada.
    """
    missing, truncated, warnings = [], [], []
    for canvas in canvases:
        missing.extend(c for c in canvas.missing if c not in missing)
        truncated.extend(c for c in canvas.truncated if c not in truncated)
        warnings.extend(a for a in canvas.warnings if a not in warnings)
    return {"missing": missing, "truncated": truncated, "warnings": warnings}


def render_element_layout_pdf(layout, data=None, user=None, batch=None):
    """Devuelve ``(bytes_pdf, report)``.

    Con ``batch`` —una lista de diccionarios de datos— se genera un PDF de
    varias páginas, un rótulo por página. Es el caso de un depósito que
    despacha doscientos paquetes: un solo archivo y una sola orden de
    impresión, en vez de doscientas peticiones.
    """
    from . import pdf

    data_sets = batch if batch is not None else [data]
    canvases = [resolve(layout, d, user) for d in data_sets]
    return pdf.draw(canvases), _build_report(canvases)


def render_element_layout_png(layout, data=None, user=None, dpi=None):
    """Devuelve ``(bytes_png, report)``.

    Sin ``data`` sale la vista previa, con cada variable dibujada como su
    etiqueta. Es lo que consume el editor.
    """
    from . import png

    canvas = resolve(layout, data, user)
    return png.draw(canvas, dpi=dpi), _build_report([canvas])
