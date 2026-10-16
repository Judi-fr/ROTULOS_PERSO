"""Reglas de la cola de impresión. Las vistas (del comerciante y del agente)
solo validan la entrada y llaman acá.

Ciclo de un trabajo: ``pending`` (en cola) → el agente lo toma → ``printing``
→ ``printed`` o ``error``. ``error`` y ``cancelled`` se pueden reintentar
(vuelven a ``pending``); ``pending`` y ``error`` se pueden cancelar. Un
trabajo en ``printing`` no se cancela: el agente ya lo está mandando.

**Un trabajo que el agente tomó y nunca confirmó NO se reimprime solo**: pasado
``PRINT_JOB_CONFIRM_TIMEOUT_SECONDS`` queda en ``error`` y la persona decide
reintentar. El agente pudo haberse cortado después de imprimir, y 30 rótulos
duplicados son un problema peor que un aviso.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from pathlib import PurePath

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.documents.models import Document

from .models import PrintAgent, Printer, PrintJob

TOKEN_PREFIX = "rpa_"
STALE_ERROR = (
    "El agente tomó el trabajo pero no avisó si imprimió (¿se cortó la PC o la red?). "
    "Revisá la impresora antes de reintentar, para no duplicar rótulos."
)


class PrintingError(Exception):
    """Un pedido que no se puede cumplir; el mensaje es para el usuario."""


# --- Agentes -------------------------------------------------------------------


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_agent(owner, name):
    """Crea el agente y devuelve ``(agent, token)``: el token solo existe acá."""
    secret = secrets.token_urlsafe(32)
    prefix = secrets.token_hex(4)
    token = f"{TOKEN_PREFIX}{prefix}_{secret}"
    agent = PrintAgent.objects.create(
        owner=owner, name=name, token_prefix=prefix, token_hash=hash_token(token)
    )
    return agent, token


def agent_for_token(token: str):
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    return (
        PrintAgent.objects.select_related("owner")
        .filter(token_hash=hash_token(token), is_active=True, owner__is_active=True)
        .first()
    )


def touch_agent(agent, version=""):
    values = {"last_seen_at": timezone.now()}
    if version:
        values["last_version"] = version[:60]
    PrintAgent.objects.filter(pk=agent.pk).update(**values)


# --- Trabajos ------------------------------------------------------------------


def document_format(document) -> str:
    return PurePath(document.file.name or "").suffix.lower().lstrip(".")


def create_job(owner, printer, document) -> PrintJob:
    """Encola ``document`` (un lote ya generado) para ``printer``."""
    if printer.owner_id != owner.id or not printer.is_active:
        raise PrintingError("Esa impresora no existe.")
    if document.user_id != owner.id or not document.is_active:
        raise PrintingError("Ese documento no existe.")
    if document.status != Document.Status.READY or not document.file:
        raise PrintingError("El documento todavía no está listo para imprimir.")
    if document_format(document) != printer.format:
        raise PrintingError(
            f"«{printer.name}» imprime {printer.format.upper()} y este documento es "
            f"{document_format(document).upper() or 'de otro tipo'}: generalo en el formato de la impresora."
        )
    if printer.agent_id is None:
        raise PrintingError(f"«{printer.name}» no tiene un agente asignado: elegilo en Impresoras.")
    return PrintJob.objects.create(
        owner=owner, printer=printer, document=document, label_count=document.item_count
    )


def expire_stale_jobs(agent=None):
    """Pasa a ``error`` lo que quedó en ``printing`` sin confirmación."""
    limit = timezone.now() - timedelta(seconds=settings.PRINT_JOB_CONFIRM_TIMEOUT_SECONDS)
    stale = PrintJob.objects.filter(status=PrintJob.Status.PRINTING, claimed_at__lt=limit)
    if agent is not None:
        stale = stale.filter(agent=agent)
    return stale.update(status=PrintJob.Status.ERROR, error_message=STALE_ERROR, finished_at=timezone.now())


def claim_next_job(agent):
    """El trabajo más viejo en cola de una impresora de este agente, ya marcado
    como ``printing``; ``None`` si no hay. El cambio de estado es condicional
    (``status=pending`` en el UPDATE): si dos agentes piden a la vez, solo uno
    se lo lleva."""
    expire_stale_jobs(agent)
    candidates = (
        PrintJob.objects.filter(
            status=PrintJob.Status.PENDING,
            printer__agent=agent,
            printer__is_active=True,
            owner=agent.owner,
        )
        .order_by("created_at", "pk")
        .values_list("pk", flat=True)[:5]
    )
    for job_id in candidates:
        with transaction.atomic():
            now = timezone.now()
            taken = PrintJob.objects.filter(pk=job_id, status=PrintJob.Status.PENDING).update(
                status=PrintJob.Status.PRINTING, agent=agent, claimed_at=now, error_message=""
            )
            if taken:
                job = PrintJob.objects.select_related("printer", "document").get(pk=job_id)
                PrintJob.objects.filter(pk=job_id).update(attempts=job.attempts + 1)
                job.attempts += 1
                return job
    return None


def finish_job(agent, job, *, ok, error=""):
    if job.agent_id != agent.id or job.status != PrintJob.Status.PRINTING:
        raise PrintingError("Ese trabajo no está en impresión por este agente.")
    job.status = PrintJob.Status.PRINTED if ok else PrintJob.Status.ERROR
    job.error_message = "" if ok else (str(error).strip()[:2000] or "La impresora no aceptó el trabajo.")
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error_message", "finished_at", "updated_at"])
    return job


def retry_job(job):
    if job.status not in (PrintJob.Status.ERROR, PrintJob.Status.CANCELLED):
        raise PrintingError("Solo se reintenta un trabajo con error o cancelado.")
    if job.document is None or not job.document.file:
        raise PrintingError("El documento de este trabajo ya no existe: generalo de nuevo.")
    if job.printer is None or not job.printer.is_active:
        raise PrintingError("La impresora de este trabajo ya no existe.")
    job.status = PrintJob.Status.PENDING
    job.error_message = ""
    job.claimed_at = None
    job.finished_at = None
    job.save(update_fields=["status", "error_message", "claimed_at", "finished_at", "updated_at"])
    return job


def cancel_job(job):
    if job.status not in (PrintJob.Status.PENDING, PrintJob.Status.ERROR):
        raise PrintingError("Solo se cancela un trabajo en cola o con error.")
    job.status = PrintJob.Status.CANCELLED
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at", "updated_at"])
    return job


def set_default_printer(printer):
    """Una sola predeterminada por dueño."""
    Printer.objects.filter(owner=printer.owner, is_default=True).exclude(pk=printer.pk).update(is_default=False)
