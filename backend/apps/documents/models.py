"""Documentos generados por el sistema.

Hoy el único tipo es un lote de rótulos (``labels_batch``, ver
``apps.labels.batch_views``), pero el modelo no asume eso: ``kind`` deja
lugar a otros tipos de documento más adelante (import/export, reportes).

Un ``Document`` se crea en ``processing`` ANTES de arrancar a generar el
archivo (para que quede un registro aunque el proceso no llegue a
terminar) y termina en ``ready`` (con el archivo puesto) o ``failed`` (con
``error_message``) — nunca se queda a mitad de camino sin que alguno de
los dos campos lo explique.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class Document(models.Model):
    class Kind(models.TextChoices):
        LABELS_BATCH = "labels_batch", "Lote de rótulos"

    class Status(models.TextChoices):
        PROCESSING = "processing", "Procesando"
        READY = "ready", "Listo"
        FAILED = "failed", "Falló"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    # Legible para el usuario, ej. "Rótulos 12/09 (35 envíos)".
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=30, choices=Kind.choices, default=Kind.LABELS_BATCH)
    file = models.FileField(upload_to="documents/%Y/%m/", null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    # Cuántos rótulos (u otros ítems) entraron efectivamente en el archivo.
    item_count = models.PositiveIntegerField(default=0)
    size_bytes = models.PositiveIntegerField(default=0)
    # Qué falló (o qué se omitió, en un lote parcialmente exitoso) — nunca
    # se deja al usuario con un "falló" sin explicación.
    error_message = models.TextField(blank=True, default="")
    # Soft-delete, igual criterio que Label/User: un documento nunca se
    # destruye de verdad.
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "documento"
        verbose_name_plural = "documentos"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.user.email})"
