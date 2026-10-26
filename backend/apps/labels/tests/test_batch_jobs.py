"""Lotes de rótulos en segundo plano (T#298, ``batch_jobs.py``): más de
``LABELS_BATCH_SYNC_MAX_ITEMS`` se encolan y los genera el worker con el
mismo código que un lote chico."""

import shutil
import tempfile
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.documents.models import Document
from apps.orders.models import Address, Order
from apps.printing.models import Printer, PrintJob
from apps.printing.services import create_agent

from ..batch_jobs import run_pending_batches
from apps.accounts.tests.team_helpers import new_business

User = get_user_model()


@override_settings(LABELS_BATCH_SYNC_MAX_ITEMS=2)
class BatchJobTests(APITestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        media_override = override_settings(MEDIA_ROOT=media)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.user = User.objects.create_user(username="lote@example.com", email="lote@example.com", password="Clave123!")
        self.user.groups.add(Group.objects.get_or_create(name="subscriber")[0])
        self.biz = new_business(self.user)
        self.headers = {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(self.user).access_token}"}
        address = Address.objects.create(user=self.biz, recipient_name="María", street="Corrientes", number="1", city="CABA")
        self.orders = [Order.objects.create(user=self.biz, address=address) for _ in range(3)]

    def batch(self, orders=None, **extra):
        ids = [order.pk for order in (orders or self.orders)]
        return self.client.post("/api/v1/labels/batch/", {"order_ids": ids, **extra}, format="json", **self.headers)

    def test_small_batch_is_still_immediate(self):
        response = self.batch(self.orders[:2])
        self.assertEqual(response.status_code, 202, response.data)
        self.assertFalse(response.data["queued"])
        self.assertEqual(response.data["status"], "ready")

    def test_large_batch_is_queued_and_the_worker_generates_it_in_order(self):
        response = self.batch()
        self.assertTrue(response.data["queued"])
        self.assertEqual(response.data["status"], "processing")
        document = Document.objects.get(pk=response.data["id"])
        self.assertEqual(document.job["ids"], [order.pk for order in self.orders])

        out = StringIO()
        call_command("run_integrations_worker", "--once", stdout=out)
        self.assertIn("Lotes de rótulos generados: 1", out.getvalue())

        document.refresh_from_db()
        self.assertEqual((document.status, document.item_count, document.job), ("ready", 3, {}))
        self.assertTrue(document.file.name.endswith(".pdf"))
        self.assertEqual(run_pending_batches(), 0)

    def test_an_order_deleted_meanwhile_is_reported(self):
        document_id = self.batch().data["id"]
        self.orders[1].delete()
        run_pending_batches()
        document = Document.objects.get(pk=document_id)
        self.assertEqual((document.status, document.item_count), ("ready", 2))
        self.assertIn("1 pedido(s) ya no existían", document.error_message)

    def test_large_batch_to_a_printer_queues_the_print_job_when_ready(self):
        agent, _ = create_agent(self.user, "PC")
        printer = Printer.objects.create(owner=self.biz, agent=agent, name="Zebra", format="zpl", dpmm=8, host="10.0.0.5")
        response = self.batch(printer_id=printer.pk)
        self.assertTrue(response.data["queued"])
        self.assertIsNone(response.data["print_job"])
        self.assertFalse(PrintJob.objects.exists())

        run_pending_batches()

        job = PrintJob.objects.get()
        self.assertEqual((job.printer, job.status, job.label_count), (printer, "pending", 3))
        self.assertTrue(job.document.file.name.endswith(".zpl"))

    def test_a_claim_that_never_finished_is_retried_then_failed(self):
        document = Document.objects.get(pk=self.batch().data["id"])
        stale = (timezone.now() - timedelta(hours=1)).isoformat()

        Document.objects.filter(pk=document.pk).update(job={**document.job, "attempts": 1, "claimed_at": stale})
        self.assertEqual(run_pending_batches(), 1)
        self.assertEqual(Document.objects.get(pk=document.pk).status, "ready")

        other = Document.objects.get(pk=self.batch().data["id"])
        Document.objects.filter(pk=other.pk).update(job={**other.job, "attempts": 3, "claimed_at": stale})
        run_pending_batches()
        other.refresh_from_db()
        self.assertEqual(other.status, "failed")
        self.assertIn("se cortó varias veces", other.error_message)

    def test_a_fresh_claim_is_left_alone(self):
        document = Document.objects.get(pk=self.batch().data["id"])
        Document.objects.filter(pk=document.pk).update(
            job={**document.job, "attempts": 1, "claimed_at": timezone.now().isoformat()}
        )
        self.assertEqual(run_pending_batches(), 0)
        self.assertEqual(Document.objects.get(pk=document.pk).status, "processing")
