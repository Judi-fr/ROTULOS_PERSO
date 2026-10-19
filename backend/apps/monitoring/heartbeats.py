"""Latidos de los procesos de fondo.

El worker da una vuelta cada pocos segundos: escribir en la base en cada
vuelta sería ruido, así que ``beat`` recuerda en memoria cuándo escribió y no
vuelve a hacerlo antes de ``MONITORING_HEARTBEAT_EVERY_SECONDS``.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import Heartbeat

WORKER = "worker"
MONITOR = "monitor"
ALERT_MAIL = "alert_mail"

_last_written: dict[str, object] = {}


def beat(name, *, now=None, force=False, info=None):
    """Registra que ``name`` está vivo. Devuelve True si escribió en la base."""
    now = now or timezone.now()
    every = timedelta(seconds=settings.MONITORING_HEARTBEAT_EVERY_SECONDS)
    last = _last_written.get(name)
    if not force and last is not None and now - last < every:
        return False
    defaults = {"last_seen": now}
    if info is not None:
        defaults["info"] = info
    Heartbeat.objects.update_or_create(name=name, defaults=defaults)
    _last_written[name] = now
    return True


def last_seen(name):
    row = Heartbeat.objects.filter(name=name).first()
    return row.last_seen if row else None


def age_seconds(name, *, now=None):
    """Segundos desde el último latido, o None si nunca latió."""
    seen = last_seen(name)
    if seen is None:
        return None
    return max(0, ((now or timezone.now()) - seen).total_seconds())


def is_stale(name, max_age_seconds, *, now=None):
    age = age_seconds(name, now=now)
    return age is None or age > max_age_seconds


_last_backup_pass = None


def worker_tick(now=None):
    """Lo que hace el worker en cada vuelta: latir y, si el proceso de
    alertas (``run_monitor``) no late, correr él las alertas.

    Así una sola caída no deja al sistema sin avisos: si muere el monitor
    avisa el worker, y si muere el worker avisa el monitor. Si se cae el
    servidor entero no avisa nadie desde adentro: para eso está la sonda
    pública ``/api/v1/health/system/`` y un monitor externo (ver CLAUDE.md).
    """
    global _last_backup_pass
    from .alerts import run_alerts

    now = now or timezone.now()
    beat(WORKER, now=now)
    if not is_stale(MONITOR, settings.MONITORING_MONITOR_STALE_SECONDS, now=now):
        return None
    interval = timedelta(seconds=settings.MONITORING_INTERVAL_SECONDS)
    if _last_backup_pass is not None and now - _last_backup_pass < interval:
        return None
    _last_backup_pass = now
    return run_alerts(now)
