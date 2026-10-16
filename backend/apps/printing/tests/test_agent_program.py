"""El programa del agente (``agent/rotulos_print_agent.py``) de verdad: contra
un servidor en vivo y una "impresora" que es un socket escuchando, como una
Zebra en el puerto 9100."""

import importlib.util
import os
import shutil
import socket
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import LiveServerTestCase, SimpleTestCase, override_settings

from apps.documents.models import Document

from ..models import Printer, PrintJob
from ..services import create_agent, create_job

AGENT_PATH = Path(__file__).resolve().parents[1] / "agent" / "rotulos_print_agent.py"
spec = importlib.util.spec_from_file_location("rotulos_print_agent", AGENT_PATH)
agent_program = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_program)

User = get_user_model()
ZPL = b"^XA^FO50,50^A0N,40,40^FDMaria^FS^XZ"


class FakeNetworkPrinter:
    """Escucha en un puerto libre y guarda lo que le mandan."""

    def __init__(self):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.received = b""
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        connection, _ = self.server.accept()
        with connection:
            while chunk := connection.recv(4096):
                self.received += chunk
        self.server.close()


class AgentEndToEndTests(LiveServerTestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        media_override = override_settings(MEDIA_ROOT=media)
        media_override.enable()
        self.addCleanup(media_override.disable)

        self.user = User.objects.create_user(username="pc@example.com", email="pc@example.com", password="Clave123!")
        self.agent, self.token = create_agent(self.user, "PC del depósito")
        self.document = Document.objects.create(user=self.user, name="Rótulos", status=Document.Status.READY, item_count=1)
        self.document.file.save("rotulos.zpl", ContentFile(ZPL), save=True)

    def api(self):
        return agent_program.Api(f"{self.live_server_url}/api/v1", self.token)

    def test_prints_to_a_network_printer_and_reports_success(self):
        printer_socket = FakeNetworkPrinter()
        printer = Printer.objects.create(
            owner=self.user, agent=self.agent, name="Zebra", format="zpl", dpmm=8, host="127.0.0.1", port=printer_socket.port
        )
        job = create_job(self.user, printer, self.document)

        self.assertEqual(agent_program.run(self.api(), interval=0, once=True), 0)
        printer_socket.thread.join(timeout=5)

        self.assertEqual(printer_socket.received, ZPL)
        job.refresh_from_db()
        self.assertEqual(job.status, PrintJob.Status.PRINTED)
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.last_version.startswith(agent_program.VERSION))

    def test_unreachable_printer_is_reported_as_error(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]
        printer = Printer.objects.create(
            owner=self.user, agent=self.agent, name="Apagada", format="zpl", dpmm=8, host="127.0.0.1", port=closed_port
        )
        job = create_job(self.user, printer, self.document)

        agent_program.run(self.api(), interval=0, once=True)

        job.refresh_from_db()
        self.assertEqual(job.status, PrintJob.Status.ERROR)
        self.assertIn(f"127.0.0.1:{closed_port}", job.error_message)

    def test_revoked_token_stops_the_agent(self):
        self.agent.is_active = False
        self.agent.save()
        self.assertEqual(agent_program.run(self.api(), interval=0, once=True), 2)


class SystemCommandTests(SimpleTestCase):
    def test_linux_and_mac_use_lp_and_raw_for_zpl(self):
        self.assertEqual(agent_program.system_command("/tmp/a.zpl", "Zebra", "zpl", system="Linux"), ["lp", "-d", "Zebra", "-o", "raw", "/tmp/a.zpl"])
        self.assertEqual(agent_program.system_command("/tmp/a.pdf", "Laser", "pdf", system="Darwin"), ["lp", "-d", "Laser", "/tmp/a.pdf"])

    def test_windows_zpl_goes_raw_to_the_shared_printer(self):
        command = agent_program.system_command("C:\\t\\a.zpl", "Zebra", "zpl", system="Windows")
        self.assertEqual(command, ["cmd", "/c", "copy", "/b", "C:\\t\\a.zpl", "\\\\localhost\\Zebra"])

    def test_custom_command(self):
        with patch.dict(os.environ, {"ROTULOS_PRINT_COMMAND": "miimpresion --cola {printer} {file}"}):
            command = agent_program.system_command("/tmp/a.pdf", "Laser", "pdf", system="Linux")
        self.assertEqual(command, ["miimpresion", "--cola", "Laser", "/tmp/a.pdf"])
