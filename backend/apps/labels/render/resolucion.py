"""Convierte una plantilla más los datos de un envío en una lista de dibujo.

Es la única capa que toma decisiones. Acá se resuelve qué valor va en cada
elemento, qué hacer si falta, cómo se genera un QR, qué pasa si un nombre no
entra en su caja. Los backends de abajo solo pintan lo que salga de acá.

Que no importe reportlab ni Pillow es deliberado: se puede testear el
renderizado entero afirmando sobre primitivas, sin generar un archivo ni
comparar imágenes.

**Las medidas de texto se toman siempre con las métricas de Helvetica**, aun
cuando el PNG se dibuje con otra tipografía. Podría parecer un descuido pero
es lo que se quiere: así el truncado se decide una sola vez y el PNG de la
vista previa muestra exactamente el mismo texto que va a salir impreso. Si
cada backend midiera con su fuente, la previsualización mentiría.
"""

import logging

from reportlab.pdfbase import pdfmetrics

from apps.labels.estilos import ESTILO_POR_DEFECTO, estilo_efectivo
from apps.labels.models import TipoDato, TipoElemento

from . import codigos, fuentes
from .primitivas import Imagen, Lienzo, Linea, Rectangulo, Texto

logger = logging.getLogger(__name__)

PT_POR_MM = 72 / 25.4

# Por debajo de esto el texto deja de ser legible en un rótulo impreso; antes
# de achicar más, se trunca.
TAMANO_PT_MINIMO = 4

# Proporción de la altura de la fuente que queda por encima de la línea base.
# Es una aproximación (varía por tipografía) y solo se usa para centrar el
# texto verticalmente dentro de su caja, donde un error de medio punto no se
# nota.
FACTOR_ASCENDENTE = 0.72


def resolver(plantilla, datos=None, usuario=None):
    """Devuelve el :class:`Lienzo` listo para dibujar.

    ``datos`` es ``{codigo_de_variable: valor}``. Si es ``None`` se arma una
    **vista previa**: cada variable se dibuja con su etiqueta entre comillas
    angulares. Es lo que necesita el editor para mostrar el diseño sin tener
    un envío de verdad.

    ``usuario`` acota la búsqueda de imágenes a los documentos de quien pide
    el render: sin eso, mandar un id ajeno en los datos alcanzaría para
    incrustar el archivo de otra cuenta en el rótulo propio.
    """
    vista_previa = datos is None
    datos = datos or {}

    lienzo = Lienzo(
        ancho_mm=float(plantilla.ancho_mm),
        alto_mm=float(plantilla.alto_mm),
        dpi=plantilla.dpi,
        color_fondo=plantilla.metadatos.get("color_fondo", "#ffffff"),
    )

    # `elementos.all()` ya viene ordenado por `orden` y después por `id`
    # (Meta.ordering), o sea en orden de pintado: el fondo primero.
    for elemento in plantilla.elementos.all():
        _resolver_elemento(elemento, lienzo, datos, vista_previa, usuario)

    return lienzo


def _resolver_elemento(elemento, lienzo, datos, vista_previa, usuario):
    """Agrega al lienzo las primitivas que produce un elemento."""
    estilo = estilo_efectivo(elemento.estilo)
    caja = (
        float(elemento.x_mm),
        float(elemento.y_mm),
        float(elemento.ancho_mm),
        float(elemento.alto_mm),
    )

    if elemento.tipo == TipoElemento.LINEA:
        lienzo.primitivas.append(
            Linea(*caja, grosor_mm=estilo["grosor_mm"], color=estilo["color"])
        )
        return

    if elemento.tipo == TipoElemento.RECUADRO:
        lienzo.primitivas.append(
            Rectangulo(*caja, grosor_mm=estilo["grosor_mm"], color=estilo["color"])
        )
        return

    if elemento.tipo == TipoElemento.TEXTO_ESTATICO:
        _agregar_texto(lienzo, caja, elemento.contenido, estilo, etiqueta=None)
        return

    # A partir de acá, tipo == variable.
    variable = elemento.variable
    valor = datos.get(variable.codigo)

    if vista_previa:
        _agregar_marcador(lienzo, caja, variable, estilo)
        return

    if valor in (None, ""):
        # Un campo vacío no es un error —la plantilla puede tener opcionales—
        # pero quien imprime 200 rótulos debería enterarse antes.
        if variable.codigo not in lienzo.faltantes:
            lienzo.faltantes.append(variable.codigo)
        return

    if variable.tipo_dato == TipoDato.QR:
        lienzo.primitivas.append(Imagen(*caja, png=codigos.generar_qr(str(valor))))
    elif variable.tipo_dato == TipoDato.CODIGO_BARRAS:
        png = codigos.generar_codigo_barras(valor)
        if png is None:
            lienzo.faltantes.append(variable.codigo)
        else:
            lienzo.primitivas.append(Imagen(*caja, png=png))
    elif variable.tipo_dato == TipoDato.IMAGEN:
        _agregar_imagen(lienzo, caja, variable, valor, usuario)
    else:
        _agregar_texto(lienzo, caja, str(valor), estilo, etiqueta=variable.codigo)


def _agregar_texto(lienzo, caja, texto, estilo, etiqueta):
    """Agrega un texto, truncándolo si no entra en su caja."""
    x, y, ancho, alto = caja
    tamano = float(estilo["tamano_pt"])
    negrita, cursiva = bool(estilo["negrita"]), bool(estilo["cursiva"])

    recortado, hubo_recorte = _truncar(texto, ancho, tamano, negrita, cursiva)
    if hubo_recorte and etiqueta and etiqueta not in lienzo.truncados:
        lienzo.truncados.append(etiqueta)

    lienzo.primitivas.append(
        Texto(
            x_mm=x, y_mm=y, ancho_mm=ancho, alto_mm=alto,
            texto=recortado,
            tamano_pt=tamano,
            negrita=negrita,
            cursiva=cursiva,
            color=estilo["color"],
            alineacion=estilo["alineacion"],
            truncado=hubo_recorte,
        )
    )


def _agregar_marcador(lienzo, caja, variable, estilo):
    """Dibuja el hueco de una variable en modo vista previa.

    Los datos gráficos (QR, imágenes) se representan con un marco y la
    etiqueta adentro, para que se vea el espacio que van a ocupar sin tener
    que generar nada.
    """
    x, y, ancho, alto = caja

    if variable.tipo_dato in (TipoDato.QR, TipoDato.CODIGO_BARRAS, TipoDato.IMAGEN):
        lienzo.primitivas.append(
            Rectangulo(x, y, ancho, alto, grosor_mm=0.2, color="#999999")
        )
        # La etiqueta se centra en el marco, con un cuerpo que no se pase del
        # alto disponible.
        tamano = max(min(alto * PT_POR_MM * 0.35, 10), TAMANO_PT_MINIMO)
        marcador = dict(estilo, tamano_pt=tamano, alineacion="centro", color="#888888")
        _agregar_texto(lienzo, caja, variable.etiqueta, marcador, etiqueta=None)
        return

    _agregar_texto(lienzo, caja, f"«{variable.etiqueta}»", estilo, etiqueta=None)


def _agregar_imagen(lienzo, caja, variable, valor, usuario):
    """Resuelve una variable de tipo imagen contra un ``Documento``.

    ``valor`` es el id de un documento ya subido. La búsqueda se acota al
    usuario que pide el render: un id ajeno no debería poder incrustarse en el
    rótulo propio.
    """
    from apps.documents.models import Documento

    if usuario is None:
        lienzo.faltantes.append(variable.codigo)
        return

    try:
        documento = Documento.objects.get(pk=int(valor), subido_por=usuario)
    except (Documento.DoesNotExist, TypeError, ValueError):
        logger.info("No se encontró el documento %r para %s", valor, variable.codigo)
        lienzo.faltantes.append(variable.codigo)
        return

    if not documento.es_imagen:
        # Un PDF no se puede pegar dentro de un rótulo como si fuera un logo.
        lienzo.faltantes.append(variable.codigo)
        return

    with documento.archivo.open("rb") as f:
        lienzo.primitivas.append(Imagen(*caja, png=f.read()))


# ---------------------------------------------------------------------------
# Medición y truncado
# ---------------------------------------------------------------------------


def ancho_texto_mm(texto, tamano_pt, negrita=False, cursiva=False):
    """Ancho que ocupa un texto, en milímetros.

    Usa las métricas reales de la fuente y no un promedio por carácter: con
    ancho variable, "MMMM" ocupa más del doble que "iiii", y contar caracteres
    haría que un texto en mayúsculas se desborde igual.
    """
    puntos = pdfmetrics.stringWidth(
        texto, fuentes.nombre_pdf(negrita, cursiva), tamano_pt
    )
    return puntos / PT_POR_MM


def _truncar(texto, ancho_mm, tamano_pt, negrita, cursiva):
    """Devuelve ``(texto, hubo_recorte)`` recortando con "…" si no entra."""
    if not texto or ancho_texto_mm(texto, tamano_pt, negrita, cursiva) <= ancho_mm:
        return texto, False

    # Se saca un carácter por vez desde el final hasta que el texto más los
    # puntos suspensivos entren. Búsqueda binaria sería más rápida, pero los
    # textos de un rótulo tienen decenas de caracteres, no miles.
    recortado = texto
    while recortado:
        recortado = recortado[:-1]
        candidato = recortado.rstrip() + "…"
        if ancho_texto_mm(candidato, tamano_pt, negrita, cursiva) <= ancho_mm:
            return candidato, True

    # La caja es tan angosta que no entra ni un carácter con puntos.
    return "…", True


def linea_base_mm(y_mm, alto_mm, tamano_pt):
    """Y de la línea base para centrar verticalmente el texto en su caja.

    Los dos backends necesitan lo mismo, así que se calcula una sola vez acá.
    Se centra en vez de alinear arriba porque las cajas que produce el
    importador suelen ser más altas que el texto, y un texto pegado al borde
    superior queda visualmente suelto.
    """
    alto_texto_mm = tamano_pt * FACTOR_ASCENDENTE / PT_POR_MM
    return y_mm + (alto_mm + alto_texto_mm) / 2


def color_rgb(hexadecimal):
    """Convierte ``#rrggbb`` a una tupla ``(r, g, b)`` de 0 a 1."""
    valor = (hexadecimal or ESTILO_POR_DEFECTO["color"]).lstrip("#")
    try:
        return tuple(int(valor[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return (0.0, 0.0, 0.0)
