"""Cola de impresión (T#322-T#328): agentes, impresoras, trabajos y el
protocolo del agente."""

import os
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit.models import AuditLog
from apps.documents.models import Document
from apps.labels.models import LabelTemplate
from apps.orders.models import Address, Order

from ..models import PrintAgent, Printer, PrintJob
from ..services import STALE_ERROR
from apps.accounts.tests.team_helpers import new_business

User = get_user_model()
ZPL = b"^XA^FO50,50^A0N,40,40^FDHola^FS^XZ"


def bearer(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(user).access_token}"}


class PrintingTestCase(APITestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        media_override = override_settings(MEDIA_ROOT=media)
        media_override.enable()
        self.addCleanup(media_override.disable)

        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(username="deposito@example.com", email="deposito@example.com", password="Clave123!")
        self.user.groups.add(subscriber)
        self.biz = new_business(self.user)
        self.other = User.objects.create_user(username="otro@example.com", email="otro@example.com", password="Clave123!")
        self.other.groups.add(subscriber)
        self.other_biz = new_business(self.other)
        self.headers = bearer(self.user)

    def create_agent(self, user=None, name="PC del depósito"):
        response = self.client.post("/api/v1/printing/agents/", {"name": name}, format="json", **bearer(user or self.user))
        self.assertEqual(response.status_code, 201, response.data)
        return PrintAgent.objects.get(pk=response.data["id"]), response.data["token"]

    def create_printer(self, agent, **extra):
        payload = {"name": "Zebra", "format": "zpl", "connection": "network", "host": "192.168.0.50", "agent": agent.pk, **extra}
        response = self.client.post("/api/v1/printing/printers/", payload, format="json", **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        return Printer.objects.get(pk=response.data["id"])

    def make_document(self, user=None, content=ZPL, ext="zpl", status=Document.Status.READY):
        document = Document.objects.create(user=user or self.biz, name="Rótulos", status=status, item_count=2)
        document.file.save(f"rotulos.{ext}", ContentFile(content), save=True)
        return document

    def agent_headers(self, token):
        return {"HTTP_AUTHORIZATION": f"Agent {token}", "HTTP_X_AGENT_VERSION": "1.0.0 Linux"}


class AgentAndPrinterTests(PrintingTestCase):
    def test_agent_token_is_shown_once_and_stored_hashed(self):
        agent, token = self.create_agent()
        self.assertTrue(token.startswith(f"rpa_{agent.token_prefix}_"))
        self.assertNotIn(token, agent.token_hash)
        listed = self.client.get("/api/v1/printing/agents/", **self.headers).data["results"]
        self.assertNotIn("token", listed[0])
        self.assertFalse(listed[0]["is_online"])
        self.assertTrue(AuditLog.objects.filter(action="printer.update", actor=self.user).exists())

    def test_revoking_an_agent_blocks_it_and_frees_its_printers(self):
        agent, token = self.create_agent()
        printer = self.create_printer(agent)
        self.client.delete(f"/api/v1/printing/agents/{agent.pk}/", **self.headers)
        printer.refresh_from_db()
        self.assertIsNone(printer.agent)
        response = self.client.get("/api/v1/printing/agent/jobs/next/", **self.agent_headers(token))
        self.assertEqual(response.status_code, 401)

    def test_printer_validation(self):
        agent, _ = self.create_agent()
        url = "/api/v1/printing/printers/"
        no_host = self.client.post(url, {"name": "Z", "format": "zpl", "connection": "network"}, format="json", **self.headers)
        self.assertIn("host", no_host.data)
        no_queue = self.client.post(url, {"name": "Z", "connection": "system"}, format="json", **self.headers)
        self.assertIn("queue", no_queue.data)
        bad_dpmm = self.client.post(url, {"name": "Z", "format": "zpl", "host": "h", "dpmm": 7}, format="json", **self.headers)
        self.assertIn("dpmm", bad_dpmm.data)
        foreign_agent, _ = self.create_agent(user=self.other)
        foreign = self.client.post(url, {"name": "Z", "host": "h", "agent": foreign_agent.pk}, format="json", **self.headers)
        self.assertIn("agent", foreign.data)

    def test_zpl_defaults_to_203_dpi_and_pdf_has_no_density(self):
        agent, _ = self.create_agent()
        self.assertEqual(self.create_printer(agent).dpmm, 8)
        self.assertIsNone(self.create_printer(agent, name="Láser", format="pdf", dpmm=12).dpmm)

    def test_first_printer_is_default_and_only_one_is(self):
        agent, _ = self.create_agent()
        first = self.create_printer(agent)
        self.assertTrue(first.is_default)
        second = self.create_printer(agent, name="Otra", is_default=True)
        first.refresh_from_db()
        self.assertTrue(second.is_default)
        self.assertFalse(first.is_default)

    def test_printers_of_others_are_invisible(self):
        agent, _ = self.create_agent()
        printer = self.create_printer(agent)
        response = self.client.get(f"/api/v1/printing/printers/{printer.pk}/", **bearer(self.other))
        self.assertEqual(response.status_code, 404)

    def test_agent_download(self):
        response = self.client.get("/api/v1/printing/agent/download/", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn("rotulos_print_agent.py", response["Content-Disposition"])
        self.assertIn(b"def main", b"".join(response.streaming_content))

    def test_windows_download_is_the_exe_when_the_server_has_it(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        missing = os.path.join(folder, "RotulosAgente.exe")
        with override_settings(PRINT_AGENT_WINDOWS_EXE=missing):
            response = self.client.get("/api/v1/printing/agent/download/?platform=windows", **self.headers)
            self.assertEqual(response.status_code, 404)
            self.assertIn("Python", response.data["detail"])
            with open(missing, "wb") as exe:
                exe.write(b"MZ-de-prueba")
            response = self.client.get("/api/v1/printing/agent/download/?platform=windows", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="RotulosAgente.exe"', response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), b"MZ-de-prueba")


class JobFlowTests(PrintingTestCase):
    def setUp(self):
        super().setUp()
        self.agent, self.token = self.create_agent()
        self.printer = self.create_printer(self.agent)

    def queue(self, document=None):
        document = document or self.make_document()
        response = self.client.post(
            "/api/v1/printing/jobs/", {"document_id": document.pk, "printer_id": self.printer.pk}, format="json", **self.headers
        )
        return response

    def test_full_cycle(self):
        job_id = self.queue().data["id"]
        response = self.client.get("/api/v1/printing/agent/jobs/next/", **self.agent_headers(self.token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], job_id)
        self.assertEqual(response.data["format"], "zpl")
        self.assertEqual(response.data["printer"]["host"], "192.168.0.50")
        self.assertEqual(response.data["printer"]["port"], 9100)

        # Ya tomado: otra consulta no lo vuelve a dar.
        self.assertEqual(self.client.get("/api/v1/printing/agent/jobs/next/", **self.agent_headers(self.token)).status_code, 204)

        file_response = self.client.get(f"/api/v1/printing/agent/jobs/{job_id}/file/", **self.agent_headers(self.token))
        self.assertEqual(file_response.content, ZPL)

        result = self.client.post(f"/api/v1/printing/agent/jobs/{job_id}/result/", {"ok": True}, format="json", **self.agent_headers(self.token))
        self.assertEqual(result.data, {"status": "printed"})
        job = PrintJob.objects.get(pk=job_id)
        self.assertEqual((job.status, job.attempts, job.agent), ("printed", 1, self.agent))
        self.assertEqual(job.label_count, 2)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.last_version, "1.0.0 Linux")
        listed = self.client.get("/api/v1/printing/agents/", **self.headers).data["results"]
        self.assertTrue(listed[0]["is_online"])

    def test_error_then_retry(self):
        job_id = self.queue().data["id"]
        self.client.get("/api/v1/printing/agent/jobs/next/", **self.agent_headers(self.token))
        self.client.post(
            f"/api/v1/printing/agent/jobs/{job_id}/result/", {"ok": False, "error": "Sin papel"}, format="json", **self.agent_headers(self.token)
        )
        listed = self.client.get("/api/v1/printing/jobs/?status=error", **self.headers).data["results"]
        self.assertEqual(listed[0]["error_message"], "Sin papel")
        self.assertEqual(listed[0]["status_display"], "Error")

        retried = self.client.post(f"/api/v1/printing/jobs/{job_id}/retry/", **self.headers)
        self.assertEqual(retried.data["status"], "pending")
        again = self.client.get("/api/v1/printing/agent/jobs/next/", **self.agent_headers(self.token))
        self.assertEqual(again.data["attempt"], 2)

    def test_cancel_only_before_printing(self):
        job_id = self.queue().data["id"]
        self.assertEqual(self.client.post(f"/api/v1/printing/jobs/{job_id}/cancel/", **self.headers).data["status"], "cancelled")
        self.assertEqual(self.client.get("/api/v1/printing/agent/jobs/next/", **self.agent_headers(self.token)).status_code, 204)

        job_id = self.queue().data["id"]
        self.client.get("/api/v1/printing/agent/jobs/next/", **self.agent_headers(self.token))
        self.assertEqual(self.client.post(f"/api/v1/printing/jobs/{job_id}/cancel/", **self.headers).status_code, 400)

    def test_unconfirmed_job_becomes_error_and_is_not_reprinted(self):
        job_id = self.queue().data["id"]
        self.client.get("/api/v1/printing/agent/jobs/next/", **self.agent_headers(self.token))
        PrintJob.objects.filter(pk=job_id).update(claimed_at=timezone.now() - timedelta(minutes=10))
        self.assertEqual(self.client.get("/api/v1/printing/agent/jobs/next/", **self.agent_headers(self.token)).status_code, 204)
        job = PrintJob.objects.get(pk=job_id)
        self.assertEqual((job.status, job.error_message), ("error", STALE_ERROR))

    def test_another_agent_sees_nothing(self):
        job_id = self.queue().data["id"]
        _, other_token = self.create_agent(name="Otra PC")
        self.assertEqual(self.client.get("/api/v1/printing/agent/jobs/next/", **self.agent_headers(other_token)).status_code, 204)
        self.assertEqual(
            self.client.get(f"/api/v1/printing/agent/jobs/{job_id}/file/", **self.agent_headers(other_token)).status_code, 404
        )

    def test_bad_token_is_401(self):
        response = self.client.get("/api/v1/printing/agent/jobs/next/", HTTP_AUTHORIZATION="Agent rpa_nada")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["WWW-Authenticate"], "Agent")

    def test_format_mismatch_and_unready_documents_are_refused(self):
        pdf = self.make_document(content=b"%PDF-1.4", ext="pdf")
        self.assertIn("ZPL", self.queue(pdf).data["detail"])
        processing = self.make_document(status=Document.Status.PROCESSING)
        self.assertIn("listo", self.queue(processing).data["detail"])
        foreign = self.make_document(user=self.other_biz)
        self.assertEqual(self.queue(foreign).status_code, 404)


class BatchToPrinterTests(PrintingTestCase):
    def test_batch_with_printer_generates_in_its_format_and_queues(self):
        agent, _ = self.create_agent()
        printer = self.create_printer(agent, dpmm=12)
        address = Address.objects.create(user=self.biz, recipient_name="María Gómez", street="Corrientes", number="1000", city="CABA", postal_code="1043")
        order = Order.objects.create(user=self.biz, address=address)
        template = LabelTemplate.objects.filter(is_public=True, is_active=True).order_by("id").first()

        response = self.client.post(
            "/api/v1/labels/batch/",
            {"order_ids": [order.pk], "template_id": template.pk, "output": "pdf", "printer_id": printer.pk},
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 202, response.data)
        self.assertIsNone(response.data["print_error"])
        job = PrintJob.objects.get(pk=response.data["print_job"]["id"])
        self.assertEqual(job.printer, printer)
        self.assertTrue(job.document.file.name.endswith(".zpl"))
        with job.document.file.open("rb") as handle:
            content = handle.read()
        # 12 puntos por mm (300 dpi): 10 cm = 1200 puntos de ancho (^PW).
        self.assertIn(b"^PW1200", content)

    def test_unknown_printer_is_400(self):
        response = self.client.post(
            "/api/v1/labels/batch/", {"order_ids": [1], "printer_id": 999}, format="json", **self.headers
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("printer_id", response.data)
