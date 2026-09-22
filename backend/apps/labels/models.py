"""Rótulos de envío de los clientes de la app.

Un "rótulo" acá no es una etiqueta de producto: es la guía que se pega al
paquete que se despacha (remitente, destinatario, domicilio, CP,
localidad/provincia, N° de pedido y QR — lo que se escanea al despachar y
al entregar). El editor (``frontend/editor_rotulos.html``)
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
    (Historia 29, ver ``apps.labels.label_rendering.build_computed_context``).

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
# Catálogo de variables + plantillas por elementos. Estas conviven con
# LabelTemplate/Label (el sistema de rótulos de encomienda con design-dict en
# cm): representan un concepto distinto y no se reemplazan entre sí. Es el
# formato que produce/consume apps.processing (importación de rótulos desde
# una foto vía Claude) — ver ElementLayout/LayoutElement/LayoutVariable
# más abajo.
# ---------------------------------------------------------------------------

# 1 pulgada = 25.4 mm. Factor único para convertir mm <-> px según el DPI.
MM_PER_INCH = 25.4


def mm_to_px(millimeters, dpi):
    """Convierte una medida en milímetros a píxeles para un DPI dado."""
    return round(millimeters / MM_PER_INCH * dpi)


class LayoutVariableType(models.TextChoices):
    """Cómo se dibuja una variable al renderizar el rótulo."""

    TEXT = "texto", "Texto"
    QR = "qr", "Código QR"
    QR_SHIPMENT = "qr_envio", "Código QR con el envío completo"
    BARCODE = "codigo_barras", "Código de barras"
    IMAGE = "imagen", "Imagen"


validate_variable_code = RegexValidator(
    r"^[a-z][a-z0-9_]*$",
    "El código debe empezar con una letra minúscula y contener solo "
    "minúsculas, números y guiones bajos (por ejemplo: numero_bulto).",
)


class LayoutVariable(models.Model):
    """Un tipo de dato que puede aparecer en un rótulo (catálogo compartido).

    ``is_system`` marca las variables con las que arranca el proyecto para
    que no se puedan borrar desde la API; ``is_active`` permite retirar una
    variable de circulación sin romper las plantillas que ya la usan.
    """

    code = models.CharField(
        "código",
        max_length=50,
        unique=True,
        validators=[validate_variable_code],
        help_text="Identificador interno en minúsculas, p. ej. numero_bulto.",
    )
    label = models.CharField(
        "etiqueta",
        max_length=100,
        help_text="Nombre legible que se muestra en la interfaz.",
    )
    description = models.TextField(
        "descripción",
        blank=True,
        default="",
        help_text="Qué representa este campo dentro del rótulo.",
    )
    data_type = models.CharField(
        "tipo de dato",
        max_length=20,
        choices=LayoutVariableType.choices,
        default=LayoutVariableType.TEXT,
    )

    is_active = models.BooleanField("activa", default=True)
    is_system = models.BooleanField(
        "del sistema",
        default=False,
        help_text="Las variables del sistema no se pueden eliminar.",
    )
    order = models.PositiveIntegerField("orden", default=0)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_layout_variables",
        verbose_name="creada por",
    )
    created_at = models.DateTimeField("creada en", auto_now_add=True)

    class Meta:
        verbose_name = "variable de rótulo"
        verbose_name_plural = "variables de rótulo"
        ordering = ["order", "label"]

    def __str__(self):
        return self.label


class ElementLayout(models.Model):
    """Plantilla de rótulo por elementos: lienzo físico + resolución + metadatos.

    Distinto de ``LabelTemplate`` (design-dict en cm, editor de
    ``frontend/editor_rotulos.html``): acá el lienzo se guarda en
    milímetros/DPI y el contenido son filas ``LayoutElement`` posicionadas,
    no un dict abierto. Es el formato que produce la importación de rótulos
    por foto (``apps.processing``). Las propiedades ``width_px``/``height_px``
    derivan el tamaño en píxeles sin almacenarlo (y sin desincronizarse).
    """

    class Orientation(models.TextChoices):
        VERTICAL = "vertical", "Vertical"
        HORIZONTAL = "horizontal", "Horizontal"

    name = models.CharField("nombre", max_length=120)
    description = models.TextField("descripción", blank=True, default="")

    width_mm = models.DecimalField(
        "ancho (mm)",
        max_digits=7,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    height_mm = models.DecimalField(
        "alto (mm)",
        max_digits=7,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    dpi = models.PositiveIntegerField("DPI", default=300)
    orientation = models.CharField(
        "orientación",
        max_length=20,
        choices=Orientation.choices,
        default=Orientation.VERTICAL,
    )
    metadata = models.JSONField("metadatos", blank=True, default=dict)
    is_active = models.BooleanField("activa", default=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_element_layouts",
        verbose_name="creada por",
    )
    created_at = models.DateTimeField("creada en", auto_now_add=True)
    updated_at = models.DateTimeField("actualizada en", auto_now=True)

    class Meta:
        verbose_name = "plantilla"
        verbose_name_plural = "plantillas"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.width_mm}×{self.height_mm} mm @ {self.dpi} DPI)"

    @property
    def width_px(self):
        return mm_to_px(float(self.width_mm), self.dpi)

    @property
    def height_px(self):
        return mm_to_px(float(self.height_mm), self.dpi)


class LayoutElementType(models.TextChoices):
    """Qué clase de cosa es un elemento colocado sobre la plantilla."""

    VARIABLE = "variable", "Variable"
    STATIC_TEXT = "texto_estatico", "Texto estático"
    LINE = "linea", "Línea"
    BOX = "recuadro", "Recuadro"

class LayoutElement(models.Model):
    """Una cosa dibujada dentro de un ``ElementLayout``, posicionada en mm.

    Según ``element_type`` cambia qué campo lleva el contenido: variable /
    texto estático / línea / recuadro. La coherencia se valida en
    ``clean()`` y con una CheckConstraint en la base.
    """

    layout = models.ForeignKey(
        ElementLayout,
        on_delete=models.CASCADE,
        related_name="elements",
        verbose_name="plantilla",
    )
    element_type = models.CharField(
        "tipo",
        max_length=20,
        choices=LayoutElementType.choices,
        default=LayoutElementType.VARIABLE,
    )

    variable = models.ForeignKey(
        LayoutVariable,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="elements",
        verbose_name="variable",
    )
    content = models.TextField("contenido", blank=True, default="")

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
    width_mm = models.DecimalField(
        "ancho (mm)",
        max_digits=7,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    height_mm = models.DecimalField(
        "alto (mm)",
        max_digits=7,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    # JSONField abierto: sus CLAVES (tamano_pt, alineacion, negrita, cursiva,
    # fuente, color, grosor_mm — ver apps.labels.styles) son formato de datos,
    # igual que LabelTemplate.design, y quedan en español a propósito.
    style = models.JSONField("estilo", blank=True, default=dict)
    order = models.PositiveIntegerField("orden", default=0)

    class Meta:
        verbose_name = "elemento de plantilla"
        verbose_name_plural = "elementos de plantilla"
        ordering = ["order", "id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        element_type=LayoutElementType.VARIABLE,
                        variable__isnull=False,
                        content="",
                    )
                    | (
                        models.Q(
                            element_type=LayoutElementType.STATIC_TEXT,
                            variable__isnull=True,
                        )
                        & ~models.Q(content="")
                    )
                    | models.Q(
                        element_type__in=[LayoutElementType.LINE, LayoutElementType.BOX],
                        variable__isnull=True,
                        content="",
                    )
                ),
                name="labels_element_coherent_with_type",
            ),
        ]

    def __str__(self):
        if self.element_type == LayoutElementType.VARIABLE:
            return f"{self.variable} en plantilla {self.layout_id}"
        return f"{self.get_element_type_display()} en plantilla {self.layout_id}"

    def clean(self):
        errors = {}

        if self.element_type == LayoutElementType.VARIABLE:
            if self.variable_id is None:
                errors["variable"] = "Un elemento de tipo variable debe indicar cuál."
            if self.content:
                errors["content"] = (
                    "Un elemento de tipo variable no lleva contenido fijo: "
                    "el texto lo aporta el dato al imprimir."
                )
        elif self.element_type == LayoutElementType.STATIC_TEXT:
            if not self.content:
                errors["content"] = "Un texto estático necesita contenido."
            if self.variable_id is not None:
                errors["variable"] = (
                    "Un texto estático no se asocia a una variable del catálogo."
                )
        else:  # linea / recuadro
            if self.variable_id is not None:
                errors["variable"] = (
                    f"Un elemento de tipo {self.get_element_type_display().lower()} "
                    "no lleva variable."
                )
            if self.content:
                errors["content"] = (
                    f"Un elemento de tipo {self.get_element_type_display().lower()} "
                    "no lleva contenido."
                )

        if errors:
            raise ValidationError(errors)
