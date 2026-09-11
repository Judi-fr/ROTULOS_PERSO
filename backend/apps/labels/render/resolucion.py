"""Convierte una plantilla más los datos de un envío en una lista de dibujo.

Es la única capa que toma decisiones. Acá se resuelve qué valor va en cada
elemento, qué hacer si falta, cómo se genera un QR, qué pasa si un nombre no
entra en su caja. Los backends de abajo solo pintan lo que salga de acá.

Que no importe reportlab ni Pillow es deliberado: se puede testear el
renderizado entero afirmando sobre primitivas, sin generar un archivo ni
comparar imágenes.

**Las medidas de texto se toman una sola vez acá**, con las métricas de la
familia elegida en el estilo, y valen para los dos backends. Así el truncado
se decide en un solo lugar y el PNG de la vista previa muestra exactamente el
mismo texto que va a salir impreso; si cada backend midiera con lo suyo, la
previsualización mentiría. Que la familia entre en la cuenta no es un detalle:
Courier es bastante más ancha que Helvetica al mismo cuerpo.
"""

import json
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

# Lado mínimo de un módulo de QR impreso, en milímetros, para que un lector de
# mano común lo levante. Por debajo de 0.4 mm hace falta un escáner dedicado y
# una impresión muy limpia; el rótulo de una encomienda no es ninguna de las
# dos cosas. Es el número que convierte "el QR entró en la caja" en "el QR se
# puede leer", que no es lo mismo.
MODULO_QR_MINIMO_MM = 0.4

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

    # Qué variables de esta plantilla son imágenes. Lo necesita el QR del
    # envío para dejarlas afuera: en los datos son ids de documentos de este
    # sistema, que no le dicen nada a quien escanea el paquete. Se calcula una
    # vez acá porque los elementos ya vienen prefetcheados y recorrerlos nada
    # cuesta, mientras que averiguarlo dentro del QR obligaría a otra consulta.
    codigos_imagen = {
        e.variable.codigo
        for e in plantilla.elementos.all()
        if e.tipo == TipoElemento.VARIABLE
        and e.variable_id
        and e.variable.tipo_dato == TipoDato.IMAGEN
    }

    # `elementos.all()` ya viene ordenado por `orden` y después por `id`
    # (Meta.ordering), o sea en orden de pintado: el fondo primero.
    for elemento in plantilla.elementos.all():
        _resolver_elemento(
            elemento, lienzo, datos, vista_previa, usuario, codigos_imagen
        )

    return lienzo


def _resolver_elemento(
    elemento, lienzo, datos, vista_previa, usuario, codigos_imagen=frozenset()
):
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
        # Se reporta por id y no por su contenido porque el aviso viaja en una
        # cabecera HTTP separada por comas: un texto con acentos o con una coma
        # la rompería. Con el id, el cliente encuentra el elemento en la
        # plantilla que ya tiene.
        _agregar_texto(
            lienzo, caja, elemento.contenido, estilo,
            etiqueta=f"texto_estatico#{elemento.pk}",
        )
        return

    # A partir de acá, tipo == variable.
    variable = elemento.variable
    valor = datos.get(variable.codigo)

    if vista_previa:
        _agregar_marcador(lienzo, caja, variable, estilo)
        return

    # El QR del envío es el único que no espera un dato propio: su contenido lo
    # arma con los de todos los demás. Por eso se atiende antes del control de
    # "valor vacío", que para él no significa nada.
    if variable.tipo_dato == TipoDato.QR_ENVIO:
        _agregar_qr_envio(lienzo, caja, variable, datos, codigos_imagen)
        return

    if valor in (None, ""):
        # Un campo vacío no es un error —la plantilla puede tener opcionales—
        # pero quien imprime 200 rótulos debería enterarse antes.
        if variable.codigo not in lienzo.faltantes:
            lienzo.faltantes.append(variable.codigo)
        return

    if variable.tipo_dato == TipoDato.QR:
        _agregar_qr(lienzo, caja, variable.codigo, str(valor))
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


def _agregar_qr(lienzo, caja, codigo, contenido):
    """Pega un QR y avisa si le quedaron los módulos demasiado finos.

    Que un QR entre en su caja no quiere decir que se pueda leer: cuanto más
    datos lleva, más módulos tiene, y con la caja fija cada módulo se achica.
    Pasado cierto punto el código sale impecable en pantalla y ningún lector
    lo levanta. Como no rompe nada, sin este aviso se descubre con el paquete
    ya despachado.
    """
    png, modulos = codigos.generar_qr(contenido)
    lienzo.primitivas.append(Imagen(*caja, png=png))

    # El QR es cuadrado y se pega centrado conservando su proporción, así que
    # el lado que manda es el menor de la caja.
    lado_mm = min(caja[2], caja[3])
    modulo_mm = lado_mm / modulos if modulos else 0
    if modulo_mm < MODULO_QR_MINIMO_MM:
        aviso = f"qr_denso:{codigo}"
        if aviso not in lienzo.avisos:
            lienzo.avisos.append(aviso)
        logger.info(
            "QR %s: %s módulos en %.1f mm dan %.2f mm por módulo (mínimo %.2f)",
            codigo, modulos, lado_mm, modulo_mm, MODULO_QR_MINIMO_MM,
        )


def payload_envio(datos, excluir=()):
    """Arma el contenido del QR del envío: un JSON compacto con todo el dato.

    Decisiones y por qué:

    - **JSON y no texto suelto** porque del otro lado hay un sistema leyendo,
      no una persona. Un WMS que recibe ``{"destinatario":"..."}`` sabe qué es
      cada cosa; uno que recibe tres líneas sueltas tiene que adivinar.
    - **Sin espacios** (``separators``): cada carácter de más son módulos de
      más, y los módulos de más son lo que vuelve ilegible el código.
    - **``ensure_ascii=False``** por lo mismo: "Gómez" son seis bytes en UTF-8
      y catorce caracteres si se escapa como ``Gómez``. El QR codifica
      bytes, así que escapar solo agranda.
    - **Ordenado por clave** para que el mismo envío produzca siempre el mismo
      código: si no, dos impresiones del mismo rótulo darían QR distintos y
      cualquier comparación se vuelve imposible.

    ``excluir`` deja afuera lo que no tiene sentido codificar: el propio QR y
    las imágenes, que en los datos son ids de documentos de este sistema y no
    significan nada para quien escanea el paquete.
    """
    utiles = {
        clave: str(valor)
        for clave, valor in sorted(datos.items())
        if clave not in excluir and valor not in (None, "")
    }
    return json.dumps(utiles, ensure_ascii=False, separators=(",", ":"))


def _agregar_qr_envio(lienzo, caja, variable, datos, codigos_imagen=frozenset()):
    """QR que lleva adentro todos los datos del envío, no un número suelto."""
    contenido = payload_envio(datos, {variable.codigo} | set(codigos_imagen))

    # Un JSON vacío ("{}") es un QR que no dice nada: se reporta como dato
    # faltante, igual que cualquier otra variable sin valor.
    if len(contenido) <= 2:
        if variable.codigo not in lienzo.faltantes:
            lienzo.faltantes.append(variable.codigo)
        return

    _agregar_qr(lienzo, caja, variable.codigo, contenido)


def _agregar_texto(lienzo, caja, texto, estilo, etiqueta):
    """Agrega un texto, truncándolo si no entra en su caja."""
    x, y, ancho, alto = caja
    tamano = float(estilo["tamano_pt"])
    negrita, cursiva = bool(estilo["negrita"]), bool(estilo["cursiva"])
    fuente = estilo.get("fuente")

    recortado, hubo_recorte = _truncar(texto, ancho, tamano, negrita, cursiva, fuente)
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
            fuente=fuente,
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

    if variable.tipo_dato in (
        TipoDato.QR, TipoDato.QR_ENVIO, TipoDato.CODIGO_BARRAS, TipoDato.IMAGEN
    ):
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


def ancho_texto_mm(texto, tamano_pt, negrita=False, cursiva=False, fuente=None):
    """Ancho que ocupa un texto, en milímetros.

    Usa las métricas reales de la fuente y no un promedio por carácter: con
    ancho variable, "MMMM" ocupa más del doble que "iiii", y contar caracteres
    haría que un texto en mayúsculas se desborde igual.

    Se mide con la familia que se va a imprimir: Courier es bastante más ancha
    que Helvetica al mismo cuerpo, así que medir todo con una sola haría que
    un texto en monoespaciada se desborde sin que nadie lo reporte.
    """
    puntos = pdfmetrics.stringWidth(
        texto, fuentes.nombre_pdf(negrita, cursiva, fuente), tamano_pt
    )
    return puntos / PT_POR_MM


def _truncar(texto, ancho_mm, tamano_pt, negrita, cursiva, fuente=None):
    """Devuelve ``(texto, hubo_recorte)`` recortando con "…" si no entra."""
    if not texto or ancho_texto_mm(texto, tamano_pt, negrita, cursiva, fuente) <= ancho_mm:
        return texto, False

    # Se saca un carácter por vez desde el final hasta que el texto más los
    # puntos suspensivos entren. Búsqueda binaria sería más rápida, pero los
    # textos de un rótulo tienen decenas de caracteres, no miles.
    recortado = texto
    while recortado:
        recortado = recortado[:-1]
        candidato = recortado.rstrip() + "…"
        if ancho_texto_mm(candidato, tamano_pt, negrita, cursiva, fuente) <= ancho_mm:
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
