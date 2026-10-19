"""``/api/v1/monitoring/``: el panel "Estado del sistema" (admin, ``monitoring.view``)."""

from __future__ import annotations

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.role_permissions import HasRolePermission

from . import heartbeats
from .alerts import alert_recipients, send_test_mail
from .checks import run_checks, worst
from .models import AlertState, Heartbeat


class MonitoringPermissionMixin:
    def get_permissions(self):
        return [HasRolePermission("monitoring.view")]


class MonitoringStatusView(MonitoringPermissionMixin, APIView):
    """GET status/ — cada chequeo con su detalle, los latidos y las alertas activas."""

    def get(self, request):
        now = timezone.now()
        results = run_checks(now)
        beats = {row.name: row for row in Heartbeat.objects.all()}
        alerts = {row.key: row for row in AlertState.objects.exclude(failing_since=None)}
        mail = beats.get(heartbeats.ALERT_MAIL)
        return Response(
            {
                "status": worst(result.status for result in results),
                "checked_at": now,
                "checks": [
                    {
                        **result.as_dict(),
                        "failing_since": alerts[result.key].failing_since if result.key in alerts else None,
                        "notified_at": alerts[result.key].notified_at if result.key in alerts else None,
                    }
                    for result in results
                ],
                "processes": {
                    name: beats[name].last_seen if name in beats else None
                    for name in (heartbeats.WORKER, heartbeats.MONITOR)
                },
                "alerts": {
                    "recipients": alert_recipients(),
                    "configured_recipients": bool([e for e in settings.MONITORING_ALERT_EMAILS if e]),
                    "last_mail": (
                        {"at": mail.last_seen, **{k: mail.info.get(k) for k in ("ok", "subject", "error")}}
                        if mail
                        else None
                    ),
                    "after_seconds": settings.MONITORING_ALERT_AFTER_SECONDS,
                    "reminder_hours": settings.MONITORING_REMINDER_HOURS,
                },
            }
        )


class MonitoringTestMailView(MonitoringPermissionMixin, APIView):
    """POST test-mail/ — manda un mail de prueba a quienes reciben las alertas."""

    def post(self, request):
        try:
            recipients = send_test_mail(requested_by=request.user.email or request.user.username)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001 - el admin necesita leer el motivo
            return Response(
                {"detail": f"No se pudo mandar el mail: {exc}"}, status=status.HTTP_502_BAD_GATEWAY
            )
        return Response({"sent_to": recipients})
