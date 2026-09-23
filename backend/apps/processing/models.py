"""Modelos de la app processing: lecturas de rótulos hechas por el agente.

Un ``LabelImport`` es el registro de haberle pedido a Claude que interprete
un ``UploadedLabelFile`` (archivo subido). La importación **no crea la
plantilla**: devuelve una *propuesta* que el usuario revisa y guarda como
``apps.labels.ElementLayout`` en un paso posterior.
"""

from django.conf import settings
from django.db import models


class LabelImportStatus(models.TextChoices):
    """En qué punto del proceso está una lectura."""

    PENDING = "pendiente", "Pendiente"
    PROCESSING = "procesando", "Procesando"
    COMPLETED = "completada", "Completada"
    ERROR = "error", "Error"


class LabelImport(models.Model):
    """Una lectura de un rótulo hecha por el modelo."""

    uploaded_file = models.ForeignKey(
        "documents.UploadedLabelFile",
        on_delete=models.CASCADE,
        related_name="label_imports",
        verbose_name="documento",
    )
    status = models.CharField(
        "estado",
        max_length=20,
        choices=LabelImportStatus.choices,
        default=LabelImportStatus.PENDING,
    )

    model_name = models.CharField("modelo", max_length=60, blank=True, default="")

    # JSONField con el cuerpo que acepta ElementLayoutSerializer: sus claves
    # de nivel superior (name, description, width_mm, height_mm, dpi,
    # orientation, elements...) son las del modelo, en inglés. Lo que va
    # dentro de cada "style" queda en español a propósito (ver
    # apps.labels.styles). Se manda tal cual a
    # POST /api/v1/labels/element-layouts/ una vez revisada, salvo la clave
    # "_revision", que es solo para que el usuario controle la lectura.
    proposal = models.JSONField("propuesta", blank=True, null=True)

    raw_response = models.JSONField(
        "respuesta cruda", blank=True, null=True
    )

    error = models.TextField("error", blank=True, default="")

    input_tokens = models.PositiveIntegerField("tokens de entrada", default=0)
    output_tokens = models.PositiveIntegerField("tokens de salida", default=0)
    request_id = models.CharField("request id", max_length=100, blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="label_imports",
        verbose_name="creada por",
    )
    created_at = models.DateTimeField("creada en", auto_now_add=True)
    finished_at = models.DateTimeField("finalizada en", null=True, blank=True)

    class Meta:
        verbose_name = "importación de rótulo"
        verbose_name_plural = "importaciones de rótulo"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Importación {self.pk} ({self.get_status_display()})"

    @property
    def confidence(self):
        """Qué tan segura dijo estar la lectura, de 0 a 1, o ``None``."""
        if not self.raw_response:
            return None
        return self.raw_response.get("confidence")
