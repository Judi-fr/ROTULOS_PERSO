"""Generación de códigos QR y de barras como PNG en memoria.

Los dos backends de dibujo saben pegar una imagen, y nada más. Convirtiendo
los códigos a PNG acá, ni ``pdf.py`` ni ``png.py`` necesitan saber que existen
los códigos: para ellos un QR es una imagen como cualquier otra.

Nada se escribe en disco. Un rótulo se imprime una vez con datos que cambian
en cada envío, así que cachear no tendría a qué agarrarse.
"""

import io
import logging

import barcode
import qrcode
from barcode.writer import ImageWriter

logger = logging.getLogger(__name__)

# Simbología por defecto para códigos de barras. Code128 acepta el juego ASCII
# completo y longitud variable, que es lo que hace falta para números de
# seguimiento arbitrarios. EAN-13 y compañía exigen largo fijo y dígito
# verificador, y fallan con cualquier otro dato.
SIMBOLOGIA = "code128"


# Módulos de margen blanco alrededor del código. El estándar pide 4; 2 alcanza
# porque el rótulo tiene su propio espacio en blanco alrededor de la caja.
BORDE_MODULOS = 2


def generar_qr(valor):
    """Devuelve ``(png, modulos)`` con el código QR de ``valor``.

    ``modulos`` es cuántos cuadraditos tiene el código de lado, borde incluido.
    Lo devuelve porque quien lo pega en el rótulo es el único que sabe de qué
    tamaño físico va a quedar la caja, y con las dos cosas puede calcular
    cuánto mide cada módulo impreso — que es lo que decide si un escáner lo
    va a poder leer. Sin ese dato, meter un envío entero adentro de un QR de
    dos centímetros produce un código perfecto e ilegible.

    Corrección de errores media (~15%): un rótulo pegado en un paquete se
    raya, se moja y se arruga, así que conviene tener margen. Alta (~30%)
    obligaría a un QR más denso para el mismo dato, y en un rótulo chico eso
    se traduce en módulos demasiado finos para el escáner.
    """
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        # El tamaño real lo define la caja del elemento al pegarlo; acá solo
        # importa que la imagen tenga resolución suficiente para no verse
        # escalonada al ampliarla.
        box_size=10,
        border=BORDE_MODULOS,
    )
    qr.add_data(valor)
    qr.make(fit=True)

    buffer = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    return buffer.getvalue(), qr.modules_count + BORDE_MODULOS * 2


def generar_codigo_barras(valor):
    """Devuelve un PNG con el código de barras de ``valor``, o ``None``.

    Devuelve ``None`` si el valor no se puede codificar. No se lanza excepción
    a propósito: un dato que no entra en la simbología no debería impedir que
    se impriman los otros 199 rótulos del lote. El elemento queda vacío y
    ``resolucion`` lo reporta.
    """
    try:
        clase = barcode.get_barcode_class(SIMBOLOGIA)
        codigo = clase(str(valor), writer=ImageWriter())

        buffer = io.BytesIO()
        codigo.write(
            buffer,
            options={
                # El texto debajo de las barras lo dibuja el propio rótulo si
                # lo necesita, como un elemento aparte. Acá estorba: no
                # respeta el estilo de la plantilla.
                "write_text": False,
                "module_height": 15.0,
                "quiet_zone": 2.0,
            },
        )
        return buffer.getvalue()
    except Exception:
        # python-barcode lanza distintas excepciones según la simbología y el
        # dato; ninguna vale la pena distinguir acá, todas significan lo mismo:
        # este valor no se puede codificar.
        logger.warning("No se pudo generar el código de barras de %r", valor)
        return None
