"""Motor de renderizado de rótulos.

Convierte una plantilla más los datos de un envío en un archivo imprimible.

    from apps.labels.render import renderizar_pdf, renderizar_png

    pdf, informe = renderizar_pdf(plantilla, {"destinatario": "María Gómez"},
                                  usuario=request.user)

El motor está partido en dos etapas, y esa es la decisión que conviene tener
presente antes de tocarlo:

1. ``resolucion.py`` toma **todas** las decisiones —qué valor va en cada
   elemento, qué pasa si falta, cómo se genera un QR, qué hacer con un texto
   que no entra— y produce una lista de primitivas en milímetros.
2. ``pdf.py`` y ``png.py`` solo pintan esas primitivas.

Así la lógica existe una sola vez aunque haya dos formatos de salida, y se
puede testear sin generar archivos ni comparar imágenes: se afirma sobre la
lista de primitivas.

Si agregás un formato nuevo (SVG, ZPL para impresoras térmicas), escribís otro
backend de la etapa 2 y no tocás nada de la 1.
"""

from .primitivas import Imagen, Lienzo, Linea, Rectangulo, Texto
from .resolucion import resolver

__all__ = [
    "renderizar_pdf",
    "renderizar_png",
    "resolver",
    "Lienzo",
    "Texto",
    "Imagen",
    "Linea",
    "Rectangulo",
]


def _informe(lienzos):
    """Junta lo que hay que reportarle a quien pidió el render.

    Se acumula por lote y sin repetir: en un PDF de doscientos rótulos el
    problema suele ser el mismo doscientas veces, y listarlo doscientas veces
    no agrega nada.
    """
    faltantes, truncados, avisos = [], [], []
    for lienzo in lienzos:
        faltantes.extend(c for c in lienzo.faltantes if c not in faltantes)
        truncados.extend(c for c in lienzo.truncados if c not in truncados)
        avisos.extend(a for a in lienzo.avisos if a not in avisos)
    return {"faltantes": faltantes, "truncados": truncados, "avisos": avisos}


def renderizar_pdf(plantilla, datos=None, usuario=None, lote=None):
    """Devuelve ``(bytes_pdf, informe)``.

    Con ``lote`` —una lista de diccionarios de datos— se genera un PDF de
    varias páginas, un rótulo por página. Es el caso de un depósito que
    despacha doscientos paquetes: un solo archivo y una sola orden de
    impresión, en vez de doscientas peticiones.
    """
    from . import pdf

    juegos = lote if lote is not None else [datos]
    lienzos = [resolver(plantilla, d, usuario) for d in juegos]
    return pdf.dibujar(lienzos), _informe(lienzos)


def renderizar_png(plantilla, datos=None, usuario=None, dpi=None):
    """Devuelve ``(bytes_png, informe)``.

    Sin ``datos`` sale la vista previa, con cada variable dibujada como su
    etiqueta. Es lo que consume el editor.
    """
    from . import png

    lienzo = resolver(plantilla, datos, usuario)
    return png.dibujar(lienzo, dpi=dpi), _informe([lienzo])
