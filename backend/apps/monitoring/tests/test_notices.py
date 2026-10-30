"""Avisos al comerciante: lo que se rompió en SU comercio (tienda con error,
cuenta de transportista rechazada, lote o impresión fallidos, rótulo que
pidió la tienda y no salió), en el panel y por mail al dueño."""

from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts.tests.test_team_permissions import add_employee, bearer, make_user
from apps.carriers.models import CarrierAccount
from apps.documents.models import Document
from apps.integrations.models import StoreConnection, StoreLabelRequest
from apps.monitoring import notices
from apps.monitoring.models import MerchantNotice
from apps.printing.models import Printer, PrintJob
from apps.accounts.tests.team_helpers import new_business

URL = "/api/v1/notices/"


def store(owner, tag, **extra):
    return StoreConnection.objects.create(
        owner=owner, platform=StoreConnection.Platform.TIENDANUBE, external_store_id=f"s-{tag}", name=f"Tienda {tag}", **extra
    )


# NOTICES_MAIL_ENABLED fijo: el .env local lo apaga para no escribirles a los
# dueños de la base local, y estos tests prueban justamente el mail.
@override_settings(NOTICES_MAIL_RETRY_SECONDS=600, NOTICES_MAIL_ENABLED=True)
class NoticeTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.owner_biz = new_business(self.owner)
        self.other = make_user("otro@example.com")
        self.other_biz = new_business(self.other)

    def keys(self, user=None):
        response = self.client.get(URL, **bearer(user or self.owner))
        self.assertEqual(response.status_code, 200, response.data)
        return {row["kind"] for row in response.data}

    def test_each_problem_becomes_a_notice_with_a_link(self):
        broken = store(self.owner_biz, "1", status=StoreConnection.Status.ERROR, last_error="401 token inválido")
        CarrierAccount.objects.create(owner=self.owner_biz, carrier="andreani", is_active=False, last_error="Credenciales rechazadas")
        Document.objects.create(user=self.owner_biz, name="Lote", status=Document.Status.FAILED, error_message="Plantilla rota", store_ids=[])
        printer = Printer.objects.create(owner=self.owner_biz, name="Zebra", format="pdf")
        PrintJob.objects.create(owner=self.owner_biz, printer=printer, status=PrintJob.Status.ERROR, error_message="Sin papel")
        StoreLabelRequest.objects.create(connection=broken, external_label_id="L1", status=StoreLabelRequest.Status.FAILED, error_message="x")
        notices.sync_all()
        rows = {row["kind"]: row for row in self.client.get(URL, **bearer(self.owner)).data}
        self.assertEqual(set(rows), {"store_error", "carrier_auth", "batch_failed", "print_error", "store_label_failed"})
        self.assertEqual(rows["store_error"]["url"], "tiendas.html")
        self.assertIn("Tienda 1", rows["store_error"]["title"])
        self.assertEqual(rows["carrier_auth"]["url"], "transportista.html?carrier=andreani")

    def test_a_notice_closes_when_the_problem_is_gone(self):
        broken = store(self.owner_biz, "1", status=StoreConnection.Status.ERROR)
        notices.sync_all()
        self.assertEqual(self.keys(), {"store_error"})
        StoreConnection.objects.filter(pk=broken.pk).update(status=StoreConnection.Status.ACTIVE)
        notices.sync_all()
        self.assertEqual(self.keys(), set())
        self.assertIsNotNone(MerchantNotice.objects.get().resolved_at)

    def test_old_failures_are_not_notices(self):
        document = Document.objects.create(user=self.owner_biz, name="Viejo", status=Document.Status.FAILED, store_ids=[])
        Document.objects.filter(pk=document.pk).update(updated_at=timezone.now() - timedelta(days=4))
        notices.sync_all()
        self.assertEqual(self.keys(), set())

    def test_owner_gets_one_mail_per_pass_with_the_new_ones_only(self):
        store(self.owner_biz, "1", status=StoreConnection.Status.ERROR)
        CarrierAccount.objects.create(owner=self.owner_biz, carrier="oca", is_active=False, last_error="Clave")
        notices.sync_all()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["owner@example.com"])
        self.assertIn("Tienda 1", mail.outbox[0].body)
        self.assertIn("notificaciones.html", mail.outbox[0].body)
        notices.sync_all()
        self.assertEqual(len(mail.outbox), 1)  # nada nuevo, nada de mail

    def test_a_failed_mail_is_retried_later(self):
        store(self.owner_biz, "1", status=StoreConnection.Status.ERROR)
        with mock.patch("apps.monitoring.notices.send_mail", side_effect=OSError("SMTP")):
            notices.sync_all()
        self.assertIsNone(MerchantNotice.objects.get().mailed_at)
        notices.sync_all()  # dentro del tiempo de reintento: no prueba
        self.assertEqual(len(mail.outbox), 0)
        MerchantNotice.objects.update(mail_attempted_at=timezone.now() - timedelta(minutes=11))
        notices.sync_all()
        self.assertEqual(len(mail.outbox), 1)

    def test_nothing_crosses_businesses(self):
        store(self.other_biz, "x", status=StoreConnection.Status.ERROR)
        notices.sync_all()
        self.assertEqual(self.keys(), set())

    def test_employees_see_only_their_areas_and_stores(self):
        t1 = store(self.owner_biz, "1", status=StoreConnection.Status.ERROR)
        store(self.owner_biz, "2", status=StoreConnection.Status.ERROR)
        CarrierAccount.objects.create(owner=self.owner_biz, carrier="andreani", is_active=False, last_error="x")
        deposito, _ = add_employee(self.owner, "dep@example.com", profile="Depósito")
        self.assertEqual(self.keys(deposito), set())  # ni tiendas ni transportistas
        manager, member = add_employee(self.owner, "m@example.com", profile="Todo")
        member.all_stores = False
        member.save()
        member.stores.set([t1])
        notices.sync_all()
        rows = self.client.get(URL, **bearer(manager)).data
        self.assertEqual(sorted(row["title"] for row in rows if row["kind"] == "store_error"), [f"La tienda {t1.name} tiene un error de conexión"])

    def test_dismissed_notice_stays_hidden_while_the_problem_lasts(self):
        store(self.owner_biz, "1", status=StoreConnection.Status.ERROR)
        notices.sync_all()
        notice = MerchantNotice.objects.get()
        self.assertEqual(self.client.post(f"{URL}{notice.id}/dismiss/", **bearer(self.owner)).status_code, 204)
        notices.sync_all()
        self.assertEqual(self.keys(), set())

    def test_cannot_dismiss_another_business_notice(self):
        store(self.other_biz, "x", status=StoreConnection.Status.ERROR)
        notices.sync_all()
        notice = MerchantNotice.objects.get()
        self.assertEqual(self.client.post(f"{URL}{notice.id}/dismiss/", **bearer(self.owner)).status_code, 404)

    def test_the_monitor_runs_the_sync(self):
        from io import StringIO

        from django.core.management import call_command

        store(self.owner_biz, "1", status=StoreConnection.Status.ERROR)
        call_command("run_monitor", "--once", stdout=StringIO())
        self.assertTrue(MerchantNotice.objects.filter(resolved_at=None).exists())
