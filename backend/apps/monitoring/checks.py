"""Qué se mira para decir si el sistema está bien.

Cada chequeo devuelve un ``CheckResult`` con uno de tres estados:

- ``ok``;
- ``warning``: algo para mirar, sin que el servicio esté caído (un lote que
  falló ayer, una tienda con el token vencido). Se ve en el panel y NO manda
  mail: un aviso que llega por cada cosa menor termina ignorado;
- ``error``: algo no anda y no se va a arreglar solo (la base no responde, el
  worker no late, la cola no avanza). Esto sí manda mail (``alerts.py``).

Un chequeo que revienta no tumba a los demás: se informa como ``error`` con
el motivo. Todo es una consulta barata a la base, porque corre cada minuto y
también en la sonda pública ``/api/v1/health/system/``.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import asdict, dataclass
from datetime import timedelta

from django.conf import settings
from django.db import connections
from django.utils import timezone

from . import heartbeats

logger = logging.getLogger(__name__)

OK = "ok"
WARNING = "warning"
ERROR = "error"
_SEVERITY = {OK: 0, WARNING: 1, ERROR: 2}


@dataclass
class CheckResult:
    key: str
    label: str
    status: str
    detail: str = ""

    def as_dict(self):
        return asdict(self)


def worst(statuses):
    """El peor estado de una lista (``ok`` si está vacía)."""
    return max(statuses, key=_SEVERITY.__getitem__, default=OK)


def _ago(seconds):
    seconds = int(seconds)
    if seconds < 120:
        return f"{seconds} segundos"
    if seconds < 7200:
        return f"{seconds // 60} minutos"
    if seconds < 172800:
        return f"{seconds // 3600} horas"
    return f"{seconds // 86400} días"


# ---------------------------------------------------------------------------
# Chequeos
# ---------------------------------------------------------------------------


def check_database(now):
    with connections["default"].cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return OK, "Responde."


def check_worker(now):
    """El worker de integraciones: sin él no entran pedidos de las tiendas,
    no se generan los lotes grandes ni se actualiza el seguimiento."""
    age = heartbeats.age_seconds(heartbeats.WORKER, now=now)
    if age is None:
        return ERROR, "Nunca corrió: falta levantar run_integrations_worker."
    if age > settings.MONITORING_WORKER_STALE_SECONDS:
        return ERROR, f"No da señales hace {_ago(age)}: está detenido o trabado."
    return OK, f"Último latido hace {_ago(age)}."


def check_monitor(now):
    """El proceso de alertas. Si está caído el worker hace su trabajo, por
    eso es un aviso y no un error."""
    age = heartbeats.age_seconds(heartbeats.MONITOR, now=now)
    if age is None:
        return WARNING, "No está corriendo run_monitor; las alertas las manda el worker."
    if age > settings.MONITORING_MONITOR_STALE_SECONDS:
        return WARNING, f"No da señales hace {_ago(age)}; las alertas las manda el worker."
    return OK, f"Último latido hace {_ago(age)}."


def check_integration_queue(now):
    from apps.integrations.models import IntegrationEvent

    Status = IntegrationEvent.Status
    delay = timedelta(seconds=settings.MONITORING_QUEUE_DELAY_SECONDS)
    overdue = IntegrationEvent.objects.filter(status=Status.PENDING, next_attempt_at__lt=now - delay)
    overdue_count = overdue.count()
    if overdue_count:
        oldest = overdue.order_by("next_attempt_at").values_list("next_attempt_at", flat=True).first()
        return ERROR, (
            f"{overdue_count} evento(s) esperando sin procesarse, el más viejo hace "
            f"{_ago((now - oldest).total_seconds())}: la cola no avanza."
        )
    # El worker re-encola un evento trabado en "processing" pasado su
    # timeout; si sigue ahí bastante después, nadie lo está re-encolando.
    stuck_after = timedelta(seconds=settings.INTEGRATIONS_EVENT_PROCESSING_TIMEOUT_SECONDS) + delay
    stuck = IntegrationEvent.objects.filter(status=Status.PROCESSING, updated_at__lt=now - stuck_after).count()
    if stuck:
        return ERROR, f"{stuck} evento(s) trabados en «procesando»."
    failed = IntegrationEvent.objects.filter(
        status=Status.FAILED, updated_at__gte=now - timedelta(hours=24)
    ).count()
    if failed:
        return WARNING, f"{failed} evento(s) fallaron en las últimas 24 horas (ver el admin de Django)."
    pending = IntegrationEvent.objects.filter(status=Status.PENDING).count()
    return OK, f"Al día ({pending} pendiente(s) en término)." if pending else "Al día."


def check_label_batches(now):
    from apps.documents.models import Document

    # Un lote en segundo plano se reintenta LABELS_BATCH_JOB_MAX_ATTEMPTS
    # veces de LABELS_BATCH_JOB_TIMEOUT_SECONDS: pasado eso más un margen,
    # "procesando" ya no es trabajo en curso sino un lote abandonado.
    limit = settings.LABELS_BATCH_JOB_TIMEOUT_SECONDS * (settings.LABELS_BATCH_JOB_MAX_ATTEMPTS + 1)
    stuck = (
        Document.objects.filter(status=Document.Status.PROCESSING, updated_at__lt=now - timedelta(seconds=limit))
        .exclude(job={})
        .count()
    )
    if stuck:
        return ERROR, f"{stuck} lote(s) de rótulos trabados en «procesando»."
    failed = Document.objects.filter(
        status=Document.Status.FAILED, updated_at__gte=now - timedelta(hours=24)
    ).count()
    if failed:
        return WARNING, f"{failed} lote(s) fallaron en las últimas 24 horas."
    return OK, "Sin lotes trabados."


def check_disk(now):
    """El disco de los archivos generados (PDF de los lotes, logos)."""
    # MEDIA_ROOT recién existe cuando se guardó el primer archivo.
    path = settings.MEDIA_ROOT if os.path.isdir(settings.MEDIA_ROOT) else settings.BASE_DIR
    usage = shutil.disk_usage(path)
    free_percent = usage.free * 100 / usage.total
    detail = f"Libre: {usage.free / 1024 ** 3:.1f} GB ({free_percent:.0f} %)."
    if free_percent < settings.MONITORING_DISK_ERROR_PERCENT:
        return ERROR, f"Casi sin espacio. {detail}"
    if free_percent < settings.MONITORING_DISK_WARNING_PERCENT:
        return WARNING, f"Poco espacio. {detail}"
    return OK, detail


def check_alert_mail(now):
    """Si las alertas pueden salir. Sin esto, todo lo demás falla en silencio."""
    from .alerts import alert_recipients

    backend = settings.EMAIL_BACKEND
    if backend.endswith("smtp.EmailBackend") and not settings.EMAIL_HOST:
        return WARNING, "No hay servidor de correo (EMAIL_HOST): las alertas no pueden salir."
    if not alert_recipients():
        return WARNING, "No hay a quién avisar: ningún admin con email ni MONITORING_ALERT_EMAILS."
    last = heartbeats.Heartbeat.objects.filter(name=heartbeats.ALERT_MAIL).first()
    if last and not last.info.get("ok", True):
        return ERROR, f"El último mail de alerta no salió: {last.info.get('error', '')}"
    if backend.endswith(("console.EmailBackend", "locmem.EmailBackend", "dummy.EmailBackend")):
        return WARNING, f"El correo está configurado para no enviar de verdad ({backend.rsplit('.', 2)[-2]})."
    return OK, "Configurado."


def check_stores(now):
    from apps.integrations.models import StoreConnection

    count = StoreConnection.objects.filter(status=StoreConnection.Status.ERROR).count()
    if count:
        return WARNING, f"{count} tienda(s) con errores de conexión."
    return OK, "Sin tiendas con errores."


def check_carriers(now):
    from apps.carriers.models import CarrierAccount

    count = CarrierAccount.objects.filter(is_active=False).exclude(last_error="").count()
    if count:
        return WARNING, f"{count} cuenta(s) de transportista desactivadas por un error."
    return OK, "Sin cuentas con errores."


CHECKS = [
    ("database", "Base de datos", check_database),
    ("worker", "Worker de integraciones", check_worker),
    ("integration_queue", "Cola de integraciones", check_integration_queue),
    ("label_batches", "Lotes de rótulos", check_label_batches),
    ("disk", "Espacio en disco", check_disk),
    ("alert_mail", "Mail de alertas", check_alert_mail),
    ("monitor", "Proceso de alertas", check_monitor),
    ("stores", "Tiendas conectadas", check_stores),
    ("carriers", "Cuentas de transportistas", check_carriers),
]


def run_checks(now=None):
    """Corre todos los chequeos y devuelve la lista de ``CheckResult``."""
    now = now or timezone.now()
    results = []
    for key, label, func in CHECKS:
        try:
            status, detail = func(now)
        except Exception as exc:  # noqa: BLE001 - un chequeo roto se informa, no tumba al resto
            logger.exception("El chequeo de monitoreo %s falló.", key)
            status, detail = ERROR, f"No se pudo verificar: {exc}"
        results.append(CheckResult(key, label, status, detail))
    return results
