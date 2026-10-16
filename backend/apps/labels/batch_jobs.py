"""Lotes de rótulos en segundo plano (T#298).

Un lote de más de ``LABELS_BATCH_SYNC_MAX_ITEMS`` rótulos no se dibuja dentro
del request: ``LabelBatchView`` deja en ``Document.job`` lo que hace falta
para armarlo y responde enseguida con el documento en "processing". El worker
de siempre (``run_integrations_worker``) llama a ``run_pending_batches`` en
cada vuelta y lo genera con el MISMO código (``LabelBatchView._run_batch``),
así que un lote grande sale igual que uno chico. No hay Celery ni Redis: la
cola es la propia tabla de documentos, como ``IntegrationEvent`` lo es para
las tiendas.

Lo que se guarda son ids, no pedidos: el worker los vuelve a leer, en el
mismo orden. Un pedido borrado en el medio simplemente no sale (queda dicho
en el documento). Si el worker se corta a mitad de un lote, pasado
``LABELS_BATCH_JOB_TIMEOUT_SECONDS`` el lote vuelve a la cola, hasta
``LABELS_BATCH_JOB_MAX_ATTEMPTS`` veces; después queda "falló" con el motivo.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.documents.models import Document

from .models import Label, LabelTemplate

logger = logging.getLogger(__name__)


def should_run_async(item_count: int) -> bool:
    return item_count > settings.LABELS_BATCH_SYNC_MAX_ITEMS


def queue_batch(document, items, template, output, page_layout, skipped_existing_count, dpmm, printer=None):
    """Deja el lote pendiente en ``document.job`` (el documento ya existe, en
    "processing")."""
    kind = "labels" if items and isinstance(items[0], Label) else "orders"
    document.job = {
        "kind": kind,
        "ids": [item.pk for item in items],
        "template_id": template.pk if template is not None else None,
        "output": output,
        "page_layout": page_layout,
        "skipped_existing_count": skipped_existing_count,
        "dpmm": dpmm,
        "printer_id": printer.pk if printer is not None else None,
        "attempts": 0,
        "claimed_at": None,
    }
    document.save(update_fields=["job", "updated_at"])


def _claim(document):
    """Marca el lote como tomado, de forma condicional: si dos workers
    corren a la vez, solo uno se lo lleva."""
    job = dict(document.job)
    job["attempts"] = int(job.get("attempts") or 0) + 1
    job["claimed_at"] = timezone.now().isoformat()
    now = timezone.now()
    taken = Document.objects.filter(
        pk=document.pk, updated_at=document.updated_at, status=Document.Status.PROCESSING
    ).update(job=job, updated_at=now)
    if taken:
        document.job = job
        document.updated_at = now
    return bool(taken)


def _is_due(job, now):
    claimed = parse_datetime(job.get("claimed_at") or "") if job.get("claimed_at") else None
    if claimed is None:
        return True
    return now - claimed > timedelta(seconds=settings.LABELS_BATCH_JOB_TIMEOUT_SECONDS)


def _load_items(document, job):
    ids = [int(pk) for pk in job.get("ids") or []]
    if job.get("kind") == "labels":
        queryset = Label.objects.select_related("order", "order__address", "order__user").filter(
            pk__in=ids, user=document.user
        )
    else:
        from apps.orders.models import Order

        queryset = Order.objects.select_related("address", "user", "store_connection").filter(pk__in=ids)
        # Mismo criterio que el request: lo de otro usuario solo si podía verlo todo.
        from apps.accounts.permissions_map import user_has_permission

        if not user_has_permission(document.user, "labels.view_all"):
            queryset = queryset.filter(user=document.user)
    found = {item.pk: item for item in queryset}
    return [found[pk] for pk in ids if pk in found], len(ids) - len(found)


def run_batch_job(document):
    """Genera el lote pendiente de ``document`` (ya tomado). No lanza: lo que
    falle queda en el documento."""
    from apps.printing.services import PrintingError, create_job

    from .batch_views import LabelBatchView

    job = document.job
    items, missing = _load_items(document, job)
    template = LabelTemplate.objects.filter(pk=job.get("template_id")).first() if job.get("template_id") else None
    if not items or (job.get("kind") != "labels" and template is None):
        document.status = Document.Status.FAILED
        document.error_message = "Los pedidos o la plantilla de este lote ya no existen."
    else:
        LabelBatchView()._run_batch(
            document,
            items,
            template,
            job.get("output") or "pdf",
            job.get("page_layout") or "label",
            int(job.get("skipped_existing_count") or 0),
            int(job.get("dpmm") or 8),
        )
        document.refresh_from_db()
        if missing:
            note = f"{missing} pedido(s) ya no existían cuando se generó el lote."
            document.error_message = f"{document.error_message} {note}".strip()

    printer_id = job.get("printer_id")
    if printer_id and document.status == Document.Status.READY:
        from apps.printing.models import Printer

        printer = Printer.objects.filter(pk=printer_id, owner=document.user, is_active=True).first()
        try:
            if printer is None:
                raise PrintingError("La impresora elegida ya no existe.")
            create_job(document.user, printer, document)
        except PrintingError as error:
            note = f"No se mandó a la impresora: {error}"
            document.error_message = f"{document.error_message} {note}".strip()

    document.job = {}
    document.save(update_fields=["status", "error_message", "job", "updated_at"])


def run_pending_batches(limit=3):
    """Lo que llama el worker en cada vuelta: genera hasta ``limit`` lotes
    pendientes (los más viejos primero). Devuelve cuántos procesó."""
    now = timezone.now()
    done = 0
    pending = (
        Document.objects.select_related("user")
        .filter(status=Document.Status.PROCESSING, is_active=True)
        .exclude(job={})
        .order_by("created_at", "pk")[: limit * 4]
    )
    for document in pending:
        if done >= limit:
            break
        job = document.job or {}
        if not _is_due(job, now):
            continue
        if int(job.get("attempts") or 0) >= settings.LABELS_BATCH_JOB_MAX_ATTEMPTS:
            document.status = Document.Status.FAILED
            document.error_message = "No se pudo generar el lote: se cortó varias veces. Probá de nuevo con menos rótulos."
            document.job = {}
            document.save(update_fields=["status", "error_message", "job", "updated_at"])
            continue
        if not _claim(document):
            continue
        try:
            run_batch_job(document)
        except Exception:  # noqa: BLE001 - el worker no se cae por un lote
            logger.exception("Falló el lote en segundo plano del documento %s", document.pk)
            Document.objects.filter(pk=document.pk).update(
                status=Document.Status.FAILED,
                error_message="No se pudo generar el lote por un error inesperado.",
                job={},
            )
        done += 1
    return done
