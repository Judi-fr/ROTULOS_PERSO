"""Modelos de la app processing: lecturas de rótulos hechas por el agente.

Una ``ImportacionRotulo`` es el registro de haberle pedido a Claude que
interprete un ``Documento`` (archivo subido). La importación **no crea la
plantilla**: devuelve una *propuesta* que el usuario revisa y guarda como
``apps.labels.Plantilla`` en un paso posterior.
"""

from django.conf import settings
from django.db import models


class EstadoImportacion(models.TextChoices):
    """En qué punto del proceso está una lectura."""

    PENDIENTE = "pendiente", "Pendiente"
    PROCESANDO = "procesando", "Procesando"
    COMPLETADA = "completada", "Completada"
    ERROR = "error", "Error"


class ImportacionRotulo(models.Model):
    """Una lectura de un rótulo hecha por el modelo."""

    documento = models.ForeignKey(
        "documents.Documento",
        on_delete=models.CASCADE,
        related_name="importaciones",
        verbose_name="documento",
    )
    estado = models.CharField(
        "estado",
        max_length=20,
        choices=EstadoImportacion.choices,
        default=EstadoImportacion.PENDIENTE,
    )

    modelo = models.CharField("modelo", max_length=60, blank=True, default="")

    propuesta = models.JSONField("propuesta", blank=True, null=True)

    respuesta_cruda = models.JSONField(
        "respuesta cruda", blank=True, null=True
    )

    error = models.TextField("error", blank=True, default="")

    tokens_entrada = models.PositiveIntegerField("tokens de entrada", default=0)
    tokens_salida = models.PositiveIntegerField("tokens de salida", default=0)
    request_id = models.CharField("request id", max_length=100, blank=True, default="")

    creada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="importaciones_rotulo",
        verbose_name="creada por",
    )
    creada_en = models.DateTimeField("creada en", auto_now_add=True)
    finalizada_en = models.DateTimeField("finalizada en", null=True, blank=True)

    class Meta:
        verbose_name = "importación de rótulo"
        verbose_name_plural = "importaciones de rótulo"
        ordering = ["-creada_en"]

    def __str__(self):
        return f"Importación {self.pk} ({self.get_estado_display()})"

    @property
    def confianza(self):
        """Qué tan segura dijo estar la lectura, de 0 a 1, o ``None``."""
        if not self.respuesta_cruda:
            return None
        return self.respuesta_cruda.get("confianza")
