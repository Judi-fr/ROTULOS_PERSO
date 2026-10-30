"""Monitoreo y alertas de disponibilidad (T#193)."""

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.documents.models import Document
from apps.integrations.models import IntegrationEvent, StoreConnection
from apps.monitoring import heartbeats
from apps.monitoring.alerts import alert_recipients, run_alerts
from apps.monitoring.checks import ERROR, OK, WARNING, run_checks
from apps.monitoring.models import AlertState, Heartbeat

User = get_user_model()


def bearer(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(user).access_token}"}


class MonitoringTestMixin:
    def setUp(self):
        super().setUp()
        heartbeats._last_written.clear()
        heartbeats._last_backup_pass = None
        self.now = timezone.now()
        self.admin = User.objects.create_user(
            username="admin@example.com", email="admin@example.com", password="Clave123!", is_staff=True
        )
        self.admin.groups.add(Group.objects.get_or_create(name="admin")[0])

    def healthy(self, now=None):
        """Deja vivos al worker y al monitor."""
        now = now or self.now
        heartbeats.beat(heartbeats.WORKER, now=now, force=True)
        heartbeats.beat(heartbeats.MONITOR, now=now, force=True)

    def status_of(self, key, now=None):
        return next(r for r in run_checks(now or self.now) if r.key == key)

    def overdue_event(self, minutes=30):
        return IntegrationEvent.objects.create(
            platform=StoreConnection.Platform.TIENDANUBE,
            event_type="orders/created",
            dedupe_key="k",
            next_attempt_at=self.now - timedelta(minutes=minutes),
        )


class CheckTests(MonitoringTestMixin, TestCase):
    def test_worker_that_never_ran_is_an_error(self):
        result = self.status_of("worker")
        self.assertEqual(result.status, ERROR)
        self.assertIn("run_integrations_worker", result.detail)

    @override_settings(MONITORING_WORKER_STALE_SECONDS=900)
    def test_worker_is_stale_after_its_threshold(self):
        heartbeats.beat(heartbeats.WORKER, now=self.now - timedelta(minutes=10), force=True)
        self.assertEqual(self.status_of("worker").status, OK)
        heartbeats.beat(heartbeats.WORKER, now=self.now - timedelta(minutes=16), force=True)
        self.assertEqual(self.status_of("worker").status, ERROR)

    def test_missing_monitor_is_only_a_warning(self):
        # Si el monitor no corre, el worker hace las alertas: no es una caída.
        self.assertEqual(self.status_of("monitor").status, WARNING)

    @override_settings(MONITORING_QUEUE_DELAY_SECONDS=900)
    def test_queue_that_does_not_move_is_an_error_and_old_failures_a_warning(self):
        self.assertEqual(self.status_of("integration_queue").status, OK)
        IntegrationEvent.objects.create(
            platform=StoreConnection.Platform.TIENDANUBE, event_type="x", dedupe_key="f", status="failed"
        )
        self.assertEqual(self.status_of("integration_queue").status, WARNING)
        # Pendiente pero todavía en término: no es problema.
        self.overdue_event(minutes=5)
        self.assertEqual(self.status_of("integration_queue").status, WARNING)
        self.overdue_event(minutes=20)
        self.assertEqual(self.status_of("integration_queue").status, ERROR)

    @override_settings(LABELS_BATCH_JOB_TIMEOUT_SECONDS=600, LABELS_BATCH_JOB_MAX_ATTEMPTS=3)
    def test_abandoned_background_batch_is_an_error(self):
        doc = Document.objects.create(user=self.admin, name="Lote", job={"order_ids": [1]})
        self.assertEqual(self.status_of("label_batches").status, OK)
        Document.objects.filter(pk=doc.pk).update(updated_at=self.now - timedelta(minutes=41))
        self.assertEqual(self.status_of("label_batches").status, ERROR)

    def test_a_check_that_crashes_is_reported_and_the_rest_still_run(self):
        from apps.monitoring import checks

        def boom(now):
            raise OSError("sin disco")

        with mock.patch.object(
            checks, "CHECKS", [("disk", "Espacio en disco", boom), ("database", "Base de datos", checks.check_database)]
        ):
            results = {r.key: r for r in run_checks(self.now)}
        self.assertEqual(results["disk"].status, ERROR)
        self.assertIn("sin disco", results["disk"].detail)
        self.assertEqual(results["database"].status, OK)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", EMAIL_HOST="")
    def test_alerts_without_a_mail_server_are_warned(self):
        result = self.status_of("alert_mail")
        self.assertEqual(result.status, WARNING)
        self.assertIn("EMAIL_HOST", result.detail)


@override_settings(
    MONITORING_ALERT_AFTER_SECONDS=180,
    MONITORING_REMINDER_HOURS=6,
    MONITORING_MAIL_RETRY_SECONDS=600,
    MONITORING_WORKER_STALE_SECONDS=900,
    MONITORING_ALERT_EMAILS=[],
)
class AlertTests(MonitoringTestMixin, TestCase):
    def kill_worker(self):
        Heartbeat.objects.filter(name=heartbeats.WORKER).update(last_seen=self.now - timedelta(hours=1))

    def test_mails_once_after_the_grace_period_then_reminds_then_reports_recovery(self):
        self.healthy()
        self.kill_worker()

        first = run_alerts(self.now)
        self.assertFalse(first["sent"])  # dentro de la gracia
        self.assertEqual(len(mail.outbox), 0)

        later = run_alerts(self.now + timedelta(minutes=4))
        self.assertTrue(later["sent"])
        self.assertEqual(later["new"], ["worker"])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Worker de integraciones", mail.outbox[0].subject)
        self.assertIn("Dejó de funcionar", mail.outbox[0].body)
        self.assertIn("estado_sistema.html", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["admin@example.com"])

        # Sigue caído: nada nuevo hasta el recordatorio.
        run_alerts(self.now + timedelta(minutes=30))
        self.assertEqual(len(mail.outbox), 1)
        reminder = run_alerts(self.now + timedelta(hours=7))
        self.assertEqual(reminder["reminders"], ["worker"])
        self.assertIn("Sigue sin funcionar", mail.outbox[1].body)

        back = self.now + timedelta(hours=8)
        heartbeats.beat(heartbeats.WORKER, now=back, force=True)
        heartbeats.beat(heartbeats.MONITOR, now=back, force=True)
        recovered = run_alerts(back)
        self.assertEqual(recovered["recovered"], ["worker"])
        self.assertTrue(mail.outbox[2].subject.endswith("Resuelto: Worker de integraciones"))
        self.assertIsNone(AlertState.objects.get(key="worker").failing_since)

    def test_a_failure_fixed_within_the_grace_period_mails_nobody(self):
        self.healthy()
        self.kill_worker()
        run_alerts(self.now)
        heartbeats.beat(heartbeats.WORKER, now=self.now + timedelta(minutes=1), force=True)
        run_alerts(self.now + timedelta(minutes=1))
        run_alerts(self.now + timedelta(minutes=10))
        self.assertEqual(len(mail.outbox), 0)

    def test_several_problems_go_in_one_mail(self):
        self.healthy()
        self.kill_worker()
        self.overdue_event(minutes=60)
        run_alerts(self.now)
        run_alerts(self.now + timedelta(minutes=4))
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(mail.outbox[0].subject.startswith("[Rótulos] 2 problemas"))

    def test_warnings_do_not_mail(self):
        # Sin monitor (warning) y con un evento fallido (warning): nada.
        heartbeats.beat(heartbeats.WORKER, now=self.now, force=True)
        IntegrationEvent.objects.create(
            platform=StoreConnection.Platform.TIENDANUBE, event_type="x", dedupe_key="f", status="failed"
        )
        run_alerts(self.now)
        heartbeats.beat(heartbeats.WORKER, now=self.now + timedelta(minutes=5), force=True)
        run_alerts(self.now + timedelta(minutes=5))
        self.assertEqual(len(mail.outbox), 0)

    def test_a_failed_send_is_retried_later_not_every_pass(self):
        self.healthy()
        self.kill_worker()
        run_alerts(self.now)
        with mock.patch("apps.monitoring.alerts.send_mail", side_effect=OSError("SMTP caído")) as send:
            run_alerts(self.now + timedelta(minutes=4))
            run_alerts(self.now + timedelta(minutes=5))  # dentro del bloqueo de reintento
            self.assertEqual(send.call_count, 1)
        self.assertIsNone(AlertState.objects.get(key="worker").notified_at)
        self.assertEqual(self.status_of("alert_mail", self.now + timedelta(minutes=5)).status, ERROR)

        retried = run_alerts(self.now + timedelta(minutes=15))
        self.assertTrue(retried["sent"])
        self.assertEqual(len(mail.outbox), 1)

    def test_the_mail_check_itself_never_mails(self):
        self.healthy()
        Heartbeat.objects.create(name=heartbeats.ALERT_MAIL, last_seen=self.now - timedelta(hours=1), info={"ok": False, "error": "x"})
        run_alerts(self.now)
        run_alerts(self.now + timedelta(minutes=10))
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(AlertState.objects.filter(key="alert_mail").exists())

    def test_recipients_are_the_admins_unless_configured(self):
        merchant = User.objects.create_user(username="tienda@example.com", email="tienda@example.com", password="Clave123!")
        merchant.groups.add(Group.objects.get_or_create(name="subscriber")[0])
        User.objects.create_user(username="ex@example.com", email="ex@example.com", is_staff=True, is_active=False)
        self.assertEqual(alert_recipients(), ["admin@example.com"])
        with override_settings(MONITORING_ALERT_EMAILS=["ops@example.com"]):
            self.assertEqual(alert_recipients(), ["ops@example.com"])

    def test_database_down_mails_only_the_configured_addresses(self):
        from apps.monitoring import alerts

        alerts._last_no_database_mail = None
        with mock.patch.object(alerts, "run_checks", side_effect=_down):
            self.assertFalse(run_alerts(self.now)["sent"])  # sin direcciones configuradas
            with override_settings(MONITORING_ALERT_EMAILS=["ops@example.com"]):
                self.assertTrue(run_alerts(self.now)["sent"])
                self.assertFalse(run_alerts(self.now + timedelta(minutes=5))["sent"])
        self.assertEqual(mail.outbox[0].to, ["ops@example.com"])
        self.assertIn("Base de datos", mail.outbox[0].subject)


def _down(now):
    from apps.monitoring.checks import CheckResult

    return [CheckResult("database", "Base de datos", ERROR, "No se pudo verificar: conexión rechazada")]


@override_settings(MONITORING_MONITOR_STALE_SECONDS=300, MONITORING_INTERVAL_SECONDS=60, MONITORING_HEARTBEAT_EVERY_SECONDS=30)
class WorkerTickTests(MonitoringTestMixin, TestCase):
    def test_worker_beats_without_writing_on_every_loop(self):
        heartbeats.beat(heartbeats.MONITOR, now=self.now, force=True)
        heartbeats.worker_tick(self.now)
        heartbeats.worker_tick(self.now + timedelta(seconds=10))
        self.assertEqual(Heartbeat.objects.get(name="worker").last_seen, self.now)
        heartbeats.worker_tick(self.now + timedelta(seconds=31))
        self.assertEqual(Heartbeat.objects.get(name="worker").last_seen, self.now + timedelta(seconds=31))

    def test_worker_runs_the_alerts_only_when_the_monitor_is_down(self):
        heartbeats.beat(heartbeats.MONITOR, now=self.now, force=True)
        self.assertIsNone(heartbeats.worker_tick(self.now))

        later = self.now + timedelta(minutes=10)
        self.assertIsNotNone(heartbeats.worker_tick(later))
        self.assertIsNone(heartbeats.worker_tick(later + timedelta(seconds=20)))  # no en cada vuelta
        self.assertIsNotNone(heartbeats.worker_tick(later + timedelta(seconds=61)))

    def test_worker_command_leaves_its_heartbeat(self):
        call_command("run_integrations_worker", "--once", stdout=StringIO())
        self.assertTrue(Heartbeat.objects.filter(name="worker").exists())

    def test_run_monitor_once_prints_every_check(self):
        out = StringIO()
        call_command("run_monitor", "--once", stdout=out)
        self.assertIn("Base de datos", out.getvalue())
        self.assertTrue(Heartbeat.objects.filter(name="monitor").exists())


# Los destinatarios por defecto (los admins), no los del .env local: con
# MONITORING_ALERT_EMAILS cargado ahí, estos tests fallaban solo en esa máquina.
@override_settings(MONITORING_ALERT_EMAILS=[])
class MonitoringApiTests(MonitoringTestMixin, APITestCase):
    def test_status_panel_is_admin_only_and_has_the_detail(self):
        merchant = User.objects.create_user(username="tienda@example.com", email="tienda@example.com", password="Clave123!")
        merchant.groups.add(Group.objects.get_or_create(name="subscriber")[0])
        self.assertEqual(self.client.get("/api/v1/monitoring/status/", **bearer(merchant)).status_code, 403)

        response = self.client.get("/api/v1/monitoring/status/", **bearer(self.admin))
        self.assertEqual(response.status_code, 200)
        checks = {c["key"]: c for c in response.data["checks"]}
        self.assertEqual(checks["worker"]["status"], ERROR)
        self.assertIn("run_integrations_worker", checks["worker"]["detail"])
        self.assertEqual(response.data["status"], ERROR)
        self.assertEqual(response.data["alerts"]["recipients"], ["admin@example.com"])

    def test_test_mail_goes_to_the_recipients(self):
        response = self.client.post("/api/v1/monitoring/test-mail/", **bearer(self.admin))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["sent_to"], ["admin@example.com"])
        self.assertEqual(mail.outbox[0].subject, "[Rótulos] Prueba de alertas")
        self.assertTrue(Heartbeat.objects.get(name=heartbeats.ALERT_MAIL).info["ok"])

    def test_test_mail_reports_why_it_failed(self):
        with mock.patch("apps.monitoring.alerts.send_mail", side_effect=OSError("auth failed")):
            response = self.client.post("/api/v1/monitoring/test-mail/", **bearer(self.admin))
        self.assertEqual(response.status_code, 502)
        self.assertIn("auth failed", response.data["detail"])

    def test_public_system_probe_says_what_fails_without_details(self):
        response = self.client.get("/api/v1/health/system/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["checks"]["worker"], ERROR)
        self.assertNotIn("run_integrations_worker", str(response.data))

        self.healthy()
        response = self.client.get("/api/v1/health/system/")
        self.assertEqual(response.status_code, 200, response.data)
