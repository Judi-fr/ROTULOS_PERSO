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

from django.conf import settings
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
