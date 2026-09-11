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
from django.core.exceptions import ValidationError
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

# ---------------------------------------------------------------------------
# Documento: archivo fuente subido para importar/procesar (integrado desde
# backend_echu). Convive con Document (documento generado por el sistema).
# ---------------------------------------------------------------------------

# Formatos que la API de Claude acepta como imagen. Se rechaza en la subida y
# no cuando ya es tarde.
TIPOS_IMAGEN = ("image/jpeg", "image/png", "image/gif", "image/webp")
TIPO_PDF = "application/pdf"
TIPOS_ACEPTADOS = TIPOS_IMAGEN + (TIPO_PDF,)

# Límite de la API para una imagen (se manda en base64, que agranda ~33%).
MAX_BYTES_IMAGEN = 5 * 1024 * 1024
MAX_BYTES_PDF = 32 * 1024 * 1024

# Firmas de los primeros bytes de cada formato.
_FIRMAS = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
)


def detectar_tipo(archivo):
    """Devuelve el tipo MIME real de un archivo subido, o ``None``."""
    posicion = archivo.tell()
    archivo.seek(0)
    cabecera = archivo.read(12)
    archivo.seek(posicion)

    for firma, tipo in _FIRMAS:
        if cabecera.startswith(firma):
            return tipo

    # WebP: "RIFF" + 4 bytes de tamaño + "WEBP".
    if cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WEBP":
        return "image/webp"

    return None


def validar_archivo(archivo):
    """Valida tipo y tamaño de un archivo subido. Devuelve el tipo MIME real."""
    tipo = detectar_tipo(archivo)

    if tipo is None:
        raise ValidationError(
            "No se reconoce el formato del archivo. Se aceptan imágenes "
            "JPEG, PNG, GIF o WebP, y PDF."
        )
    if tipo not in TIPOS_ACEPTADOS:
        raise ValidationError(f"El formato {tipo} no está soportado.")

    maximo = MAX_BYTES_PDF if tipo == TIPO_PDF else MAX_BYTES_IMAGEN
    if archivo.size > maximo:
        raise ValidationError(
            f"El archivo pesa {archivo.size / 1024 / 1024:.1f} MB y el máximo "
            f"para este formato es {maximo // 1024 // 1024} MB."
        )

    return tipo


class Documento(models.Model):
    """Una foto o un PDF de rótulo subido por un usuario para importar."""

    archivo = models.FileField("archivo", upload_to="rotulos/%Y/%m/")
    nombre_original = models.CharField(
        "nombre original",
        max_length=255,
        help_text="Nombre que tenía el archivo en la máquina del usuario.",
    )
    tipo_mime = models.CharField(
        "tipo MIME", max_length=50, choices=[(t, t) for t in TIPOS_ACEPTADOS]
    )
    tamano_bytes = models.PositiveIntegerField("tamaño (bytes)")

    subido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="documentos_fuente",
        verbose_name="subido por",
    )
    subido_en = models.DateTimeField("subido en", auto_now_add=True)

    class Meta:
        verbose_name = "documento"
        verbose_name_plural = "documentos"
        ordering = ["-subido_en"]

    def __str__(self):
        return f"{self.nombre_original} ({self.tipo_mime})"

    @property
    def es_imagen(self):
        """¿Va como bloque ``image`` (True) o como bloque ``document`` (False)?"""
        return self.tipo_mime in TIPOS_IMAGEN
