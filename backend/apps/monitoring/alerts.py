"""Alertas por mail a los admins.

Se avisa en el CAMBIO, no en cada pasada: un mail cuando un chequeo pasa a
``error`` (después de ``MONITORING_ALERT_AFTER_SECONDS`` de gracia, para que
un reinicio del worker no despierte a nadie), un recordatorio cada
``MONITORING_REMINDER_HOURS`` si sigue igual, y uno cuando se resuelve. Todo
lo de una pasada va en UN solo mail.

Los ``warning`` no mandan mail (ver ``checks.py``), y el chequeo del propio
mail tampoco: avisar por mail que el mail no sale no llega a nadie.

Si un envío falla, el estado no se marca como avisado y se reintenta, pero
no antes de ``MONITORING_MAIL_RETRY_SECONDS``: un SMTP caído no tiene que
llenar el log con un error por minuto.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import heartbeats
from .checks import ERROR, run_checks
from .models import AlertState, Heartbeat

logger = logging.getLogger(__name__)

# Chequeos que no disparan mail (ver el docstring del módulo).
NOT_MAILED = {"alert_mail"}

# Sin base no hay estado guardado ni lista de admins: se avisa a
# MONITORING_ALERT_EMAILS, a lo sumo una vez por recordatorio, por proceso.
_last_no_database_mail = None


def alert_recipients():
    """``MONITORING_ALERT_EMAILS`` si está definido; si no, los admins activos con email."""
    configured = [email for email in settings.MONITORING_ALERT_EMAILS if email]
    if configured:
        return sorted(set(configured))
    User = get_user_model()
    emails = (
        User.objects.filter(is_active=True)
        .filter(Q(groups__name="admin") | Q(is_staff=True))
        .exclude(email="")
        .values_list("email", flat=True)
        .distinct()
    )
    return sorted(set(emails))


def status_page_url():
    return f"{settings.FRONTEND_URL.rstrip('/')}/estado_sistema.html"


def _send(subject, body, recipients):
    send_mail(
        f"{settings.MONITORING_SUBJECT_PREFIX} {subject}",
        body,
        settings.DEFAULT_FROM_EMAIL,
        recipients,
        fail_silently=False,
    )


def _record_mail(now, *, ok, subject="", error="", recipients=()):
    Heartbeat.objects.update_or_create(
        name=heartbeats.ALERT_MAIL,
        defaults={
            "last_seen": now,
            "info": {"ok": ok, "subject": subject, "error": error, "recipients": list(recipients)},
        },
    )


def _mail_retry_blocked(now):
    """True si el último envío falló hace menos de MONITORING_MAIL_RETRY_SECONDS."""
    last = Heartbeat.objects.filter(name=heartbeats.ALERT_MAIL).first()
    if last is None or last.info.get("ok", True):
        return False
    return now - last.last_seen < timedelta(seconds=settings.MONITORING_MAIL_RETRY_SECONDS)


def _compose(new, reminders, recovered):
    """Asunto y cuerpo de un mail con todo lo de una pasada."""
    problems = new + reminders
    if problems:
        names = ", ".join(result.label for result in problems)
        subject = f"Problema: {names}" if len(problems) == 1 else f"{len(problems)} problemas: {names}"
    else:
        subject = "Resuelto: " + ", ".join(result.label for result in recovered)

    lines = []
    if new:
        lines.append("Dejó de funcionar:")
        lines += [f"  - {result.label}: {result.detail}" for result in new]
        lines.append("")
    if reminders:
        lines.append("Sigue sin funcionar:")
        lines += [f"  - {result.label}: {result.detail}" for result in reminders]
        lines.append("")
    if recovered:
        lines.append("Volvió a funcionar:")
        lines += [f"  - {result.label}" for result in recovered]
        lines.append("")
    lines.append(f"Estado completo: {status_page_url()}")
    lines.append("")
    lines.append("Este aviso lo manda el monitoreo de la app a los administradores.")
    return subject, "\n".join(lines)


def run_alerts(now=None):
    """Corre los chequeos y manda el mail que corresponda. Devuelve un resumen.

    ``{"results": [...], "new": [...], "reminders": [...], "recovered": [...],
    "sent": bool}`` con las claves de los chequeos.
    """
    now = now or timezone.now()
    results = run_checks(now)
    summary = {"results": results, "new": [], "reminders": [], "recovered": [], "sent": False}

    database = next(result for result in results if result.key == "database")
    if database.status == ERROR:
        summary["sent"] = _alert_without_database(now, database)
        return summary

    grace = timedelta(seconds=settings.MONITORING_ALERT_AFTER_SECONDS)
    remind_every = timedelta(hours=settings.MONITORING_REMINDER_HOURS)
    new, reminders, recovered = [], [], []

    with transaction.atomic():
        states = {
            state.key: state
            for state in AlertState.objects.select_for_update().filter(key__in=[r.key for r in results])
        }
        for result in results:
            if result.key in NOT_MAILED:
                continue
            state = states.get(result.key)
            if result.status == ERROR:
                if state is None:
                    state = AlertState.objects.create(key=result.key, failing_since=now, detail=result.detail)
                    states[result.key] = state
                elif state.failing_since is None:
                    state.failing_since = now
                    state.notified_at = None
                state.detail = result.detail
                state.save()
                if state.notified_at is None and now - state.failing_since >= grace:
                    new.append(result)
                elif state.notified_at is not None and now - state.notified_at >= remind_every:
                    reminders.append(result)
            elif state is not None and state.failing_since is not None:
                if state.notified_at is not None:
                    recovered.append(result)
                else:
                    # Falló y se arregló dentro de la gracia: nadie se enteró
                    # y no hace falta que se entere.
                    state.failing_since = None
                    state.detail = ""
                    state.save()

        summary.update(
            new=[r.key for r in new], reminders=[r.key for r in reminders], recovered=[r.key for r in recovered]
        )
        if not (new or reminders or recovered):
            return summary
        if _mail_retry_blocked(now):
            return summary

        recipients = alert_recipients()
        subject, body = _compose(new, reminders, recovered)
        if not recipients:
            logger.error("Alerta de monitoreo sin destinatarios: %s", subject)
            _record_mail(now, ok=False, subject=subject, error="No hay destinatarios.")
            return summary
        try:
            _send(subject, body, recipients)
        except Exception as exc:  # noqa: BLE001 - el monitoreo informa, no revienta
            logger.exception("No se pudo mandar el mail de alerta: %s", subject)
            _record_mail(now, ok=False, subject=subject, error=str(exc), recipients=recipients)
            return summary

        logger.warning("Alerta de monitoreo enviada: %s", subject, extra={"recipients": recipients})
        _record_mail(now, ok=True, subject=subject, recipients=recipients)
        summary["sent"] = True
        for result in new + reminders:
            state = states[result.key]
            state.notified_at = now
            state.save()
        for result in recovered:
            state = states[result.key]
            state.failing_since = None
            state.notified_at = None
            state.detail = ""
            state.save()
    return summary


def _alert_without_database(now, database):
    """Con la base caída no se puede guardar estado ni leer los admins."""
    global _last_no_database_mail
    logger.error("Monitoreo: la base de datos no responde. %s", database.detail)
    recipients = [email for email in settings.MONITORING_ALERT_EMAILS if email]
    if not recipients:
        return False
    remind_every = timedelta(hours=settings.MONITORING_REMINDER_HOURS)
    if _last_no_database_mail is not None and now - _last_no_database_mail < remind_every:
        return False
    subject, body = _compose([database], [], [])
    try:
        _send(subject, body, recipients)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo mandar el mail de alerta: %s", subject)
        return False
    _last_no_database_mail = now
    return True


def send_test_mail(requested_by=None):
    """Manda un mail de prueba a los destinatarios de las alertas.

    Devuelve la lista de destinatarios; levanta la excepción del envío para
    que la vista la muestre tal cual (es lo que el admin necesita leer).
    """
    now = timezone.now()
    recipients = alert_recipients()
    if not recipients:
        raise ValueError("No hay a quién mandarlo: ningún admin tiene email y no está definido MONITORING_ALERT_EMAILS.")
    who = f" a pedido de {requested_by}" if requested_by else ""
    subject = "Prueba de alertas"
    body = (
        f"Este es un mail de prueba del monitoreo{who}.\n\n"
        "Si te llegó, las alertas de disponibilidad también te van a llegar.\n\n"
        f"Estado completo: {status_page_url()}\n"
    )
    try:
        _send(subject, body, recipients)
    except Exception as exc:
        _record_mail(now, ok=False, subject=subject, error=str(exc), recipients=recipients)
        raise
    _record_mail(now, ok=True, subject=subject, recipients=recipients)
    return recipients
