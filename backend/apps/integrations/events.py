"""Cola de eventos de tiendas conectadas (``IntegrationEvent``).

- ``enqueue_event``: lo llama quien recibe el aviso (el webhook de la
  tienda) — guarda y vuelve enseguida.
- ``process_due_events``: lo llama el worker
  (``manage.py run_integrations_worker``) — toma los eventos vencidos y
  ejecuta el handler registrado para ``(platform, event_type)``.

Reintentos: un error inesperado vuelve el evento a ``pending`` con backoff
exponencial (``INTEGRATIONS_EVENT_RETRY_BASE_SECONDS`` * 2^(intento-1),
tope ``INTEGRATIONS_EVENT_RETRY_MAX_SECONDS``) hasta
``INTEGRATIONS_EVENT_MAX_ATTEMPTS``; ``PermanentEventError`` lo marca
``failed`` sin reintentar (reintentar no lo va a arreglar). Un evento que
quedó en ``processing`` porque el worker murió a mitad de camino se vuelve
a encolar pasado ``INTEGRATIONS_EVENT_PROCESSING_TIMEOUT_SECONDS``.

Los handlers tienen que ser idempotentes: un evento puede ejecutarse más de
una vez (worker caído, aviso repetido ya procesado).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import IntegrationEvent

logger = logging.getLogger(__name__)

# (platform, event_type) -> callable(event)
_HANDLERS = {}

_OPEN_STATUSES = (IntegrationEvent.Status.PENDING, IntegrationEvent.Status.PROCESSING)


class PermanentEventError(Exception):
    """El evento no se puede procesar y reintentar no lo va a arreglar
    (tienda desconectada, pedido inexistente): pasa directo a ``failed``."""


def register_handler(platform, event_type):
    def decorator(func):
        _HANDLERS[(platform, event_type)] = func
        return func

    return decorator


def build_dedupe_key(platform, event_type, connection_id, resource_id, payload):
    body = json.dumps(
        {
            "platform": platform,
            "event_type": event_type,
            "connection": connection_id,
            "resource_id": resource_id,
            "payload": payload,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def enqueue_event(*, platform, event_type, connection=None, resource_id="", payload=None):
    """Encola un evento. Devuelve ``(event, created)``: si el mismo evento
    ya está pendiente o procesándose, no se encola otra vez (la plataforma
    reintentó el aviso). Uno igual ya procesado SÍ se vuelve a encolar: un
    ``order/updated`` repetido puede traer cambios nuevos."""
    payload = payload or {}
    resource_id = str(resource_id or "")
    dedupe_key = build_dedupe_key(
        platform, event_type, connection.pk if connection else None, resource_id, payload
    )
    existing = IntegrationEvent.objects.filter(dedupe_key=dedupe_key, status__in=_OPEN_STATUSES).first()
    if existing is not None:
        return existing, False
    event = IntegrationEvent.objects.create(
        connection=connection,
        platform=platform,
        event_type=event_type,
        resource_id=resource_id,
        payload=payload,
        dedupe_key=dedupe_key,
    )
    return event, True


def retry_delay_seconds(attempts):
    base = getattr(settings, "INTEGRATIONS_EVENT_RETRY_BASE_SECONDS", 60)
    cap = getattr(settings, "INTEGRATIONS_EVENT_RETRY_MAX_SECONDS", 3600)
    return min(base * (2 ** max(attempts - 1, 0)), cap)


def _requeue_stuck_events(now):
    timeout = getattr(settings, "INTEGRATIONS_EVENT_PROCESSING_TIMEOUT_SECONDS", 600)
    # .update() no toca auto_now: updated_at se pone a mano.
    return IntegrationEvent.objects.filter(
        status=IntegrationEvent.Status.PROCESSING,
        updated_at__lt=now - timedelta(seconds=timeout),
    ).update(status=IntegrationEvent.Status.PENDING, next_attempt_at=now, updated_at=now)


def claim_due_events(limit=50):
    """Marca como ``processing`` (y suma un intento a) hasta ``limit``
    eventos vencidos y los devuelve. ``skip_locked`` deja correr varios
    workers contra Postgres sin tomar el mismo evento (en SQLite
    ``select_for_update`` no tiene efecto: ahí corre un solo worker)."""
    now = timezone.now()
    _requeue_stuck_events(now)
    with transaction.atomic():
        ids = list(
            IntegrationEvent.objects.select_for_update(skip_locked=True)
            .filter(status=IntegrationEvent.Status.PENDING, next_attempt_at__lte=now)
            .order_by("next_attempt_at", "id")
            .values_list("id", flat=True)[:limit]
        )
        IntegrationEvent.objects.filter(id__in=ids).update(
            status=IntegrationEvent.Status.PROCESSING,
            attempts=F("attempts") + 1,
            updated_at=now,
        )
    return list(
        IntegrationEvent.objects.filter(id__in=ids).select_related("connection").order_by("next_attempt_at", "id")
    )


def _finish(event, status, error=""):
    event.status = status
    event.last_error = (error or "")[:2000]
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "last_error", "processed_at", "updated_at"])
    return status


def process_event(event):
    """Ejecuta el handler de un evento ya reclamado. Nunca deja escapar una
    excepción: el resultado queda en el propio evento."""
    handler = _HANDLERS.get((event.platform, event.event_type))
    if handler is None:
        return _finish(
            event,
            IntegrationEvent.Status.FAILED,
            f"No hay un handler para el evento {event.platform} {event.event_type}.",
        )

    try:
        handler(event)
    except PermanentEventError as exc:
        return _finish(event, IntegrationEvent.Status.FAILED, str(exc))
    except Exception as exc:  # noqa: BLE001 - un evento roto no frena la cola
        logger.exception("Falló el evento de integración %s (intento %s)", event.pk, event.attempts)
        max_attempts = getattr(settings, "INTEGRATIONS_EVENT_MAX_ATTEMPTS", 8)
        if event.attempts >= max_attempts:
            return _finish(
                event,
                IntegrationEvent.Status.FAILED,
                f"{exc} (se agotaron los {max_attempts} intentos)",
            )
        event.status = IntegrationEvent.Status.PENDING
        event.last_error = str(exc)[:2000]
        event.next_attempt_at = timezone.now() + timedelta(seconds=retry_delay_seconds(event.attempts))
        event.save(update_fields=["status", "last_error", "next_attempt_at", "updated_at"])
        return event.status

    return _finish(event, IntegrationEvent.Status.DONE)


def process_due_events(limit=50):
    """Procesa un lote de eventos vencidos. Devuelve cuántos terminaron en
    cada estado (``{"done": n, "pending": n, "failed": n}``)."""
    counts = {
        IntegrationEvent.Status.DONE: 0,
        IntegrationEvent.Status.PENDING: 0,
        IntegrationEvent.Status.FAILED: 0,
    }
    for event in claim_due_events(limit=limit):
        counts[process_event(event)] += 1
    return {str(status): total for status, total in counts.items()}
