"""Agentes, impresoras y la cola de trabajos (ver ``__init__.py``)."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class PrintAgent(models.Model):
    """El programa que corre en una PC del comerciante y le habla a sus
    impresoras. Se autentica con un token que se muestra una sola vez y se
    guarda como SHA-256 (es aleatorio y largo: no hace falta un hash lento
    como el de las contraseñas). Revocarlo lo desactiva; no se borra, para no
    perder a qué agente iba cada trabajo."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="print_agents")
    name = models.CharField(max_length=100)
    token_prefix = models.CharField(max_length=16, db_index=True)
    token_hash = models.CharField(max_length=64, unique=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    # Lo que el agente informa de sí mismo (versión, sistema), para soporte.
    last_version = models.CharField(max_length=60, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "agente de impresión"
        verbose_name_plural = "agentes de impresión"
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name


class Printer(models.Model):
    class Format(models.TextChoices):
        PDF = "pdf", "PDF (impresora común o de etiquetas con driver)"
        ZPL = "zpl", "ZPL (térmica Zebra o compatible)"

    class Connection(models.TextChoices):
        NETWORK = "network", "En la red (IP y puerto)"
        SYSTEM = "system", "Instalada en la PC del agente"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="printers")
    agent = models.ForeignKey(PrintAgent, on_delete=models.SET_NULL, null=True, blank=True, related_name="printers")
    name = models.CharField(max_length=100)
    format = models.CharField(max_length=10, choices=Format.choices, default=Format.PDF)
    # Solo ZPL: puntos por mm (8 = 203 dpi, 12 = 300 dpi), como labels/zpl.py.
    dpmm = models.PositiveSmallIntegerField(null=True, blank=True)
    # Dónde está ("Depósito", "Mostrador"): para elegirla, no para conectarse.
    location = models.CharField(max_length=150, blank=True, default="")
    connection = models.CharField(max_length=10, choices=Connection.choices, default=Connection.NETWORK)
    host = models.CharField(max_length=255, blank=True, default="")
    port = models.PositiveIntegerField(default=9100)
    # Nombre de la cola del sistema operativo (``lpstat -p`` en Linux/Mac, el
    # nombre compartido en Windows).
    queue = models.CharField(max_length=150, blank=True, default="")
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "impresora"
        verbose_name_plural = "impresoras"
        ordering = ["-is_default", "name", "pk"]

    def __str__(self):
        return self.name


class PrintJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "En cola"
        PRINTING = "printing", "Imprimiendo"
        PRINTED = "printed", "Impreso"
        ERROR = "error", "Error"
        CANCELLED = "cancelled", "Cancelado"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="print_jobs")
    printer = models.ForeignKey(Printer, on_delete=models.SET_NULL, null=True, related_name="jobs")
    # El agente que lo tomó la última vez (para saber de dónde salió).
    agent = models.ForeignKey(PrintAgent, on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs")
    document = models.ForeignKey(
        "documents.Document", on_delete=models.SET_NULL, null=True, related_name="print_jobs"
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    label_count = models.PositiveIntegerField(default=0)
    attempts = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default="")
    claimed_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "trabajo de impresión"
        verbose_name_plural = "trabajos de impresión"
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["printer", "status"], name="print_job_printer_status")]

    def __str__(self):
        return f"Trabajo {self.pk} ({self.get_status_display()})"
