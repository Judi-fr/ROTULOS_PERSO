"""Modelos de la app labels: catálogo de variables, plantillas y sus elementos.

Hay tres piezas y conviene no confundir las dos primeras, que trabajan a
niveles distintos:

- **VariableRotulo** es el *catálogo*: qué tipos de dato puede contener un
  rótulo (destinatario, QR, código postal…). Es una tabla compartida por todas
  las plantillas, con pocas filas, y no sabe nada de posiciones.
- **Plantilla** es el lienzo: el "papel" sobre el que se imprime, con sus
  dimensiones físicas y su resolución.
- **ElementoPlantilla** es la *colocación*: qué se dibuja, dónde y con qué
  estilo, dentro de una plantilla concreta. Puede apuntar a una variable del
  catálogo o ser contenido fijo (un texto impreso, una línea, un recuadro) que
  no representa ningún dato variable.

Dicho corto: la variable dice **qué** dato es, el elemento dice **dónde y
cómo** se dibuja. Una misma variable se reutiliza en muchos elementos, incluso
más de una vez dentro de la misma plantilla.

El catálogo vive en la base y no en un enum del código porque el sistema tiene
que poder incorporar campos nuevos —los que aparecen al importar el rótulo de
un cliente— sin publicar una versión nueva.

Se trabaja en milímetros porque es la unidad natural de la impresión física;
los píxeles (que dependen del DPI) se derivan cuando hacen falta para renderizar
a imagen. Así una misma plantilla puede exportarse a distintas resoluciones sin
recalcular posiciones a mano.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models

# 1 pulgada = 25.4 mm. Factor único para convertir mm <-> px según el DPI.
MM_POR_PULGADA = 25.4


def mm_a_px(milimetros, dpi):
    """Convierte una medida en milímetros a píxeles para un DPI dado."""
    return round(milimetros / MM_POR_PULGADA * dpi)


class TipoDato(models.TextChoices):
    """Cómo se dibuja una variable al renderizar el rótulo.

    Mientras el catálogo era un enum del código, esto era implícito: quien
    escribía el renderizador sabía que ``qr`` era un código y ``destinatario``
    texto. Con un catálogo que el usuario puede ampliar, hay que decirlo
    explícitamente o una variable nueva no tendría forma de imprimirse.
    """

    TEXTO = "texto", "Texto"
    QR = "qr", "Código QR"
    # A diferencia de QR, este no toma su contenido de un dato propio: lo arma
    # con TODOS los datos del envío. Es la diferencia entre un QR que dice
    # "AR0087123456" —y obliga a consultar un sistema para saber qué es— y uno
    # que lleva encima el destinatario, el domicilio y el pedido, y se puede
    # leer aunque no haya señal en el depósito.
    QR_ENVIO = "qr_envio", "Código QR con el envío completo"
    CODIGO_BARRAS = "codigo_barras", "Código de barras"
    IMAGEN = "imagen", "Imagen"


# El código de una variable viaja a lugares donde un espacio o un acento
# rompen: claves de un diccionario de datos al imprimir, y el enum del esquema
# que se le envía al modelo de IA al leer un rótulo desde una foto.
validar_codigo_variable = RegexValidator(
    r"^[a-z][a-z0-9_]*$",
    "El código debe empezar con una letra minúscula y contener solo "
    "minúsculas, números y guiones bajos (por ejemplo: numero_bulto).",
)


class VariableRotulo(models.Model):
    """Un tipo de dato que puede aparecer en un rótulo.

    ``descripcion`` no es decorativa: explica qué representa el campo y es lo
    que permite distinguir, por ejemplo, el remitente del destinatario. La
    consume tanto la interfaz (como ayuda al usuario) como el servicio que lee
    rótulos desde una foto (como parte de las instrucciones al modelo).

    ``es_sistema`` marca las variables con las que arranca el proyecto para
    que no se puedan borrar desde la API; ``activa`` permite retirar una
    variable de circulación sin romper las plantillas que ya la usan.
    """

    codigo = models.CharField(
        "código",
        max_length=50,
        unique=True,
        validators=[validar_codigo_variable],
        help_text="Identificador interno en minúsculas, p. ej. numero_bulto.",
    )
    etiqueta = models.CharField(
        "etiqueta",
        max_length=100,
        help_text="Nombre legible que se muestra en la interfaz.",
    )
    descripcion = models.TextField(
        "descripción",
        blank=True,
        default="",
        help_text="Qué representa este campo dentro del rótulo.",
    )
    tipo_dato = models.CharField(
        "tipo de dato",
        max_length=20,
        choices=TipoDato.choices,
        default=TipoDato.TEXTO,
    )

    activa = models.BooleanField("activa", default=True)
    es_sistema = models.BooleanField(
        "del sistema",
        default=False,
        help_text="Las variables del sistema no se pueden eliminar.",
    )
    orden = models.PositiveIntegerField("orden", default=0)

    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="variables_creadas",
        verbose_name="creada por",
    )
    creada_en = models.DateTimeField("creada en", auto_now_add=True)

    class Meta:
        verbose_name = "variable de rótulo"
        verbose_name_plural = "variables de rótulo"
        ordering = ["orden", "etiqueta"]

    def __str__(self):
        return self.etiqueta


class Plantilla(models.Model):
    """Plantilla de rótulo: lienzo físico + resolución + metadatos.

    Las dimensiones se guardan en milímetros y la resolución en DPI. Las
    propiedades ``ancho_px`` / ``alto_px`` derivan el tamaño en píxeles para
    el renderizado, sin que haya que almacenarlo (y quedar desincronizado).

    ``metadatos`` es un JSON libre para datos que no ameritan una columna
    propia (por ejemplo color de fondo, márgenes de seguridad, notas del
    diseñador). Mantenerlo abierto evita migraciones por cada dato nuevo.
    """

    class Orientacion(models.TextChoices):
        VERTICAL = "vertical", "Vertical"
        HORIZONTAL = "horizontal", "Horizontal"

    nombre = models.CharField("nombre", max_length=120)
    descripcion = models.TextField("descripción", blank=True, default="")

    # Dimensiones físicas del rótulo, en milímetros.
    ancho_mm = models.DecimalField(
        "ancho (mm)",
        max_digits=7,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    alto_mm = models.DecimalField(
        "alto (mm)",
        max_digits=7,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    # Resolución de impresión. 300 DPI es el estándar de calidad para
    # impresión de rótulos/etiquetas.
    dpi = models.PositiveIntegerField(
        "DPI", default=300, validators=[MinValueValidator(1)]
    )

    orientacion = models.CharField(
        "orientación",
        max_length=10,
        choices=Orientacion.choices,
        default=Orientacion.VERTICAL,
    )

    metadatos = models.JSONField("metadatos", blank=True, default=dict)

    # Permite ocultar plantillas sin borrarlas (soft-disable).
    activa = models.BooleanField("activa", default=True)

    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="plantillas_creadas",
        verbose_name="creada por",
    )
    creada_en = models.DateTimeField("creada en", auto_now_add=True)
    actualizada_en = models.DateTimeField("actualizada en", auto_now=True)

    class Meta:
        verbose_name = "plantilla"
        verbose_name_plural = "plantillas"
        ordering = ["-creada_en"]

    def __str__(self):
        return f"{self.nombre} ({self.ancho_mm}×{self.alto_mm} mm @ {self.dpi} DPI)"

    @property
    def ancho_px(self):
        """Ancho en píxeles derivado de ``ancho_mm`` y ``dpi``."""
        return mm_a_px(float(self.ancho_mm), self.dpi)

    @property
    def alto_px(self):
        """Alto en píxeles derivado de ``alto_mm`` y ``dpi``."""
        return mm_a_px(float(self.alto_mm), self.dpi)


class TipoElemento(models.TextChoices):
    """Qué clase de cosa es un elemento colocado sobre la plantilla.

    Un rótulo no es solo datos variables: también tiene la palabra impresa
    ``DESTINATARIO:``, líneas divisorias y recuadros. Sin estos tipos, el
    rótulo impreso no podría reproducir el diseño original.
    """

    VARIABLE = "variable", "Variable"
    TEXTO_ESTATICO = "texto_estatico", "Texto estático"
    LINEA = "linea", "Línea"
    RECUADRO = "recuadro", "Recuadro"


class ElementoPlantilla(models.Model):
    """Una cosa dibujada dentro de una plantilla, posicionada en milímetros.

    Según ``tipo`` cambia qué campo lleva el contenido:

    ==================  ==========================  ===========
    tipo                variable                    contenido
    ==================  ==========================  ===========
    ``variable``        obligatoria                 vacío
    ``texto_estatico``  vacía                       obligatorio
    ``linea``           vacía                       vacío
    ``recuadro``        vacía                       vacío
    ==================  ==========================  ===========

    Esa coherencia se valida en ``clean()`` (para que la API devuelva un error
    legible) y además con una restricción en la base (para que ninguna vía de
    escritura pueda dejar una fila incoherente).

    El rectángulo es relativo a la esquina superior izquierda de la plantilla.
    ``estilo`` es un JSON con atributos de presentación; sus claves están
    definidas en ``apps.labels.estilos``. ``orden`` define la prioridad de
    dibujado (mayor orden se dibuja encima).
    """

    plantilla = models.ForeignKey(
        Plantilla,
        on_delete=models.CASCADE,
        related_name="elementos",
        verbose_name="plantilla",
    )
    tipo = models.CharField(
        "tipo",
        max_length=20,
        choices=TipoElemento.choices,
        default=TipoElemento.VARIABLE,
    )

    # Solo para tipo=variable. PROTECT: una variable en uso no se puede borrar
    # del catálogo, para no dejar plantillas con elementos huérfanos.
    variable = models.ForeignKey(
        VariableRotulo,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="elementos",
        verbose_name="variable",
    )
    # Solo para tipo=texto_estatico: el texto literal impreso en el rótulo.
    contenido = models.TextField("contenido", blank=True, default="")

    # Posición y tamaño del elemento, en milímetros, relativos al lienzo.
    x_mm = models.DecimalField(
        "x (mm)",
        max_digits=7,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    y_mm = models.DecimalField(
        "y (mm)",
        max_digits=7,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    ancho_mm = models.DecimalField(
        "ancho (mm)",
        max_digits=7,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    alto_mm = models.DecimalField(
        "alto (mm)",
        max_digits=7,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    estilo = models.JSONField("estilo", blank=True, default=dict)
    orden = models.PositiveIntegerField("orden", default=0)

    class Meta:
        verbose_name = "elemento de plantilla"
        verbose_name_plural = "elementos de plantilla"
        ordering = ["orden", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        tipo=TipoElemento.VARIABLE,
                        variable__isnull=False,
                        contenido="",
                    )
                    | (
                        models.Q(
                            tipo=TipoElemento.TEXTO_ESTATICO,
                            variable__isnull=True,
                        )
                        & ~models.Q(contenido="")
                    )
                    | models.Q(
                        tipo__in=[TipoElemento.LINEA, TipoElemento.RECUADRO],
                        variable__isnull=True,
                        contenido="",
                    )
                ),
                name="labels_elemento_coherente_con_tipo",
            ),
        ]

    def __str__(self):
        if self.tipo == TipoElemento.VARIABLE:
            return f"{self.variable} en plantilla {self.plantilla_id}"
        return f"{self.get_tipo_display()} en plantilla {self.plantilla_id}"

    def clean(self):
        """Valida la coherencia entre ``tipo``, ``variable`` y ``contenido``."""
        errores = {}

        if self.tipo == TipoElemento.VARIABLE:
            if self.variable_id is None:
                errores["variable"] = "Un elemento de tipo variable debe indicar cuál."
            if self.contenido:
                errores["contenido"] = (
                    "Un elemento de tipo variable no lleva contenido fijo: "
                    "el texto lo aporta el dato al imprimir."
                )
        elif self.tipo == TipoElemento.TEXTO_ESTATICO:
            if not self.contenido:
                errores["contenido"] = "Un texto estático necesita contenido."
            if self.variable_id is not None:
                errores["variable"] = (
                    "Un texto estático no se asocia a una variable del catálogo."
                )
        else:  # linea / recuadro
            if self.variable_id is not None:
                errores["variable"] = (
                    f"Un elemento de tipo {self.get_tipo_display().lower()} "
                    "no lleva variable."
                )
            if self.contenido:
                errores["contenido"] = (
                    f"Un elemento de tipo {self.get_tipo_display().lower()} "
                    "no lleva contenido."
                )

        if errores:
            raise ValidationError(errores)
