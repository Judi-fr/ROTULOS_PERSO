"""Rótulos de encomienda de Buspack.

Un "rótulo" acá no es una etiqueta de producto: es la guía que se pega al
paquete que viaja en el micro (remitente, destinatario, domicilio, CP,
localidad/provincia, N° de pedido y QR — lo que se escanea en el punto de
venta y en la terminal). El editor (``frontend/pedidos/diseñorotulos.html``)
ya define el formato del diseño y hay que respetarlo: un dict ``design``
cuyas claves son los campos del rótulo (``logo``, ``qr``, ``remitente``,
``destinatario``, ``domicilio``, ``cp``, ``localidad``, ``pedido``) y cuyos
valores son ``{"left": <pct>, "top": <pct>, "text": <str opcional>}`` — la
posición en PORCENTAJE del rótulo, para que el diseño sobreviva a un cambio
de tamaño.

Todavía no existe un catálogo de puntos de venta ni de destinos: eso queda
para más adelante, no lo asuma este módulo.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models, transaction


class LabelTemplate(models.Model):
    """Diseño base de rótulo, reutilizable entre varios envíos.

    ``owner=None`` identifica una plantilla del sistema (sin dueño, visible
    para todos si además es ``is_public``). Si se borra el dueño, la
    plantilla se conserva (``SET_NULL``): no depende de que la cuenta siga
    existiendo.
    """

    name = models.CharField(max_length=150)
    description = models.CharField(max_length=255, blank=True, default="")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="label_templates",
    )
    is_public = models.BooleanField(default=False)
    width_cm = models.DecimalField(max_digits=5, decimal_places=2)
    height_cm = models.DecimalField(max_digits=5, decimal_places=2)
    # Bloque "fields" del editor: posiciones por defecto de cada campo.
    design = models.JSONField(default=dict, blank=True)
    preview = models.ImageField(upload_to="labels/templates/", null=True, blank=True)
    # Soft-delete, igual criterio que Label/Document: archivar una
    # plantilla nunca la destruye (rótulos ya generados con ella siguen
    # apuntándola vía Label.template).
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "plantilla de rótulo"
        verbose_name_plural = "plantillas de rótulo"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Label(models.Model):
    """Rótulo concreto de un envío, listo para imprimir.

    Cuando ``order`` está seteado, el rótulo pertenece a un envío concreto
    del usuario (el caso central: el editor puede autocompletar
    destinatario/domicilio/CP/localidad desde la ``Address`` de ese pedido,
    y el usuario los edita después en el diseño). Si se borra la plantilla
    de origen, el rótulo conserva su propio diseño (``SET_NULL``).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="labels",
    )
    template = models.ForeignKey(
        LabelTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="labels",
    )
    name = models.CharField(max_length=150)
    # "Cliente" del editor: el DESTINATARIO de la encomienda, no el dueño
    # de la cuenta que diseña el rótulo (ese es ``user``).
    client = models.CharField(max_length=150, blank=True, default="")
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="labels",
    )
    width_cm = models.DecimalField(max_digits=5, decimal_places=2)
    height_cm = models.DecimalField(max_digits=5, decimal_places=2)
    # Fields completo del editor: posiciones + textos de cada campo.
    design = models.JSONField(default=dict, blank=True)
    logo = models.ImageField(upload_to="labels/logos/", null=True, blank=True)
    thumbnail = models.ImageField(upload_to="labels/thumbs/", null=True, blank=True)
    # Soft-delete, igual que los usuarios: un rótulo nunca se destruye.
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "rótulo"
        verbose_name_plural = "rótulos"
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.name} ({self.user.email})"


class LabelSequence(models.Model):
    """Contador de numeración secuencial para el marcador ``{{secuencia}}``
    (Historia 29, ver ``apps.labels.rendering.build_computed_context``).

    Una fila por ``owner``+``key``: hoy todo el mundo usa ``key="default"``
    (no hay todavía un lugar natural para elegir otra desde el editor), pero
    la columna queda para no tener que migrar el día que lo haya. El valor
    devuelto es ``prefix`` + el número con ceros a la izquierda según
    ``padding`` — ver ``next_value``.
    """

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    key = models.CharField(max_length=50, default="default")
    prefix = models.CharField(max_length=20, blank=True)
    padding = models.PositiveSmallIntegerField(default=6)
    current = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("owner", "key")
        verbose_name = "secuencia de rótulos"
        verbose_name_plural = "secuencias de rótulos"

    def __str__(self):
        return f"{self.owner_id}:{self.key} ({self.current})"

    @classmethod
    def next_value(cls, owner, key="default"):
        """Incrementa el contador y devuelve el valor formateado
        (``prefix`` + ceros a la izquierda). Atómico vía ``F("current") +
        1`` (una sola sentencia UPDATE) en vez de leer-sumar-guardar en
        Python: dos rótulos del mismo lote generados "al mismo tiempo"
        nunca pueden llevarse el mismo número. Un valor por RÓTULO, nunca
        uno por lote entero (el llamador pide uno por cada ítem)."""
        with transaction.atomic():
            obj, _ = cls.objects.get_or_create(owner=owner, key=key)
            cls.objects.filter(pk=obj.pk).update(current=models.F("current") + 1)
            obj.refresh_from_db(fields=["current"])
        return f"{obj.prefix}{obj.current:0{obj.padding}d}"
# ---------------------------------------------------------------------------
# Catálogo de variables + plantillas por elementos (integradas desde
# backend_echu). Estas conviven con LabelTemplate/Label (el sistema de
# rótulos de encomienda con design-dict en cm): representan un concepto
# distinto y no se reemplazan entre sí.
# ---------------------------------------------------------------------------

# 1 pulgada = 25.4 mm. Factor único para convertir mm <-> px según el DPI.
MM_POR_PULGADA = 25.4


def mm_a_px(milimetros, dpi):
    """Convierte una medida en milímetros a píxeles para un DPI dado."""
    return round(milimetros / MM_POR_PULGADA * dpi)


class TipoDato(models.TextChoices):
    """Cómo se dibuja una variable al renderizar el rótulo."""

    TEXTO = "texto", "Texto"
    QR = "qr", "Código QR"
    QR_ENVIO = "qr_envio", "Código QR con el envío completo"
    CODIGO_BARRAS = "codigo_barras", "Código de barras"
    IMAGEN = "imagen", "Imagen"


validar_codigo_variable = RegexValidator(
    r"^[a-z][a-z0-9_]*$",
    "El código debe empezar con una letra minúscula y contener solo "
    "minúsculas, números y guiones bajos (por ejemplo: numero_bulto).",
)


class VariableRotulo(models.Model):
    """Un tipo de dato que puede aparecer en un rótulo (catálogo compartido).

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
    propiedades ``ancho_px``/``alto_px`` derivan el tamaño en píxeles sin
    almacenarlo (y sin desincronizarse).
    """

    class Orientacion(models.TextChoices):
        VERTICAL = "vertical", "Vertical"
        HORIZONTAL = "horizontal", "Horizontal"

    nombre = models.CharField("nombre", max_length=120)
    descripcion = models.TextField("descripción", blank=True, default="")

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

    dpi = models.PositiveIntegerField("DPI", default=300)
    orientacion = models.CharField(
        "orientación",
        max_length=20,
        choices=Orientacion.choices,
        default=Orientacion.VERTICAL,
    )
    metadatos = models.JSONField("metadatos", blank=True, default=dict)
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
        return mm_a_px(float(self.ancho_mm), self.dpi)

    @property
    def alto_px(self):
        return mm_a_px(float(self.alto_mm), self.dpi)


class TipoElemento(models.TextChoices):
    """Qué clase de cosa es un elemento colocado sobre la plantilla."""

    VARIABLE = "variable", "Variable"
    TEXTO_ESTATICO = "texto_estatico", "Texto estático"
    LINEA = "linea", "Línea"
    RECUADRO = "recuadro", "Recuadro"

class ElementoPlantilla(models.Model):
    """Una cosa dibujada dentro de una plantilla, posicionada en milímetros.

    Según ``tipo`` cambia qué campo lleva el contenido: variable / texto
    estático / línea / recuadro. La coherencia se valida en ``clean()`` y
    con una CheckConstraint en la base.
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

    variable = models.ForeignKey(
        VariableRotulo,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="elementos",
        verbose_name="variable",
    )
    contenido = models.TextField("contenido", blank=True, default="")

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
