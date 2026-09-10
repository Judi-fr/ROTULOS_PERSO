"""Vocabulario intermedio entre la plantilla y los backends de dibujo.

Una primitiva es una orden de dibujo ya resuelta: no tiene variables sin
completar, ni estilos a medio aplicar, ni decisiones pendientes. Solo dice
"poné este texto acá, de este tamaño, en este color".

El motivo de que exista esta capa es que hay dos backends (PDF y PNG) y sin
ella habría que escribir el renderizador dos veces. Con ella, toda la lógica
—qué valor va en cada lugar, qué tamaño tiene, cómo se acomoda si no entra—
ocurre una sola vez en ``resolucion.py``, y los backends quedan tontos:
reciben primitivas y las pintan.

El efecto secundario más útil es que se puede testear el renderizado sin
generar un solo archivo: se afirma sobre la lista de primitivas, que es
comparable, legible y rápida.

**Todas las medidas van en milímetros** y el origen es la esquina superior
izquierda del rótulo, igual que en ``ElementoPlantilla``. Los backends que
usen otro sistema de coordenadas —reportlab tiene el origen abajo— se encargan
de convertir de su lado.
"""

from dataclasses import dataclass, field


@dataclass
class Texto:
    """Una línea de texto ubicada dentro de una caja.

    La caja (``ancho_mm`` × ``alto_mm``) no recorta: sirve para alinear
    horizontalmente y para centrar verticalmente. El texto ya viene truncado
    por ``resolucion`` si no entraba, y ``truncado`` deja constancia de eso.
    """

    x_mm: float
    y_mm: float
    ancho_mm: float
    alto_mm: float
    texto: str
    tamano_pt: float
    negrita: bool = False
    cursiva: bool = False
    color: str = "#000000"
    alineacion: str = "izquierda"
    # Código de familia (ver apps.labels.render.fuentes). None = la por
    # defecto. Viaja hasta acá porque los dos backends la resuelven distinto:
    # el PDF por nombre y el PNG por ruta a un archivo.
    fuente: str = None
    truncado: bool = False


@dataclass
class Imagen:
    """Un PNG ya generado, para pegar dentro de la caja indicada.

    Cubre tres casos que para el backend son el mismo: la imagen de un
    ``Documento``, un código QR generado, y un código de barras generado. Que
    los tres lleguen acá como bytes de PNG es lo que evita que los backends
    tengan que saber nada de códigos ni de archivos.

    La imagen se ajusta a la caja **preservando su proporción**, porque un QR
    deformado deja de ser escaneable.
    """

    x_mm: float
    y_mm: float
    ancho_mm: float
    alto_mm: float
    png: bytes


@dataclass
class Linea:
    """Una línea recta horizontal o vertical.

    Se describe por su caja igual que todo lo demás: si ``alto_mm`` es menor
    que ``ancho_mm`` es horizontal, y al revés vertical. Se dibuja por el
    centro de la caja, así que el grosor crece hacia ambos lados.
    """

    x_mm: float
    y_mm: float
    ancho_mm: float
    alto_mm: float
    grosor_mm: float = 0.3
    color: str = "#000000"

    @property
    def es_horizontal(self):
        return self.ancho_mm >= self.alto_mm


@dataclass
class Rectangulo:
    """Un marco sin relleno."""

    x_mm: float
    y_mm: float
    ancho_mm: float
    alto_mm: float
    grosor_mm: float = 0.3
    color: str = "#000000"


@dataclass
class Lienzo:
    """El rótulo completo: el papel más todo lo que va dibujado encima.

    Además de las primitivas trae el resultado de la resolución, que es lo que
    la API devuelve en cabeceras para que el frontend pueda avisar:

    - ``faltantes``: variables de la plantilla que no vinieron en los datos.
      No es un error (una plantilla puede tener campos opcionales), pero el
      usuario debería enterarse antes de mandar 200 rótulos a la impresora.
    - ``truncados``: textos que no entraban en su caja y se cortaron. Cortar
      en silencio un domicilio es la clase de error que termina en un paquete
      que no llega. Las variables se identifican por su código y los textos
      fijos como ``texto_estatico#<id>``: un texto fijo también se corta, y
      cuando la plantilla la propuso el importador a partir de una foto —y no
      una persona que lo vio en pantalla— nadie se entera de otro modo.
    - ``avisos``: el resto de los problemas que no impiden imprimir pero sí
      arruinan el rótulo. Hoy solo uno: un QR que quedó tan denso para el
      tamaño de su caja que un escáner no lo va a leer. Sale como código
      ASCII (``qr_denso:<codigo>``) y no como frase, porque viaja en una
      cabecera HTTP; la frase la arma el cliente.
    """

    ancho_mm: float
    alto_mm: float
    dpi: int
    color_fondo: str = "#ffffff"
    primitivas: list = field(default_factory=list)
    faltantes: list = field(default_factory=list)
    truncados: list = field(default_factory=list)
    avisos: list = field(default_factory=list)
