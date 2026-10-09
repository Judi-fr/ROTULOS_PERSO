"""``python manage.py fake_andreani_server`` — una Andreani simulada en
``http://127.0.0.1:8099`` para probar a mano, SOLO en local.

Andreani da credenciales de QA únicamente a clientes (por su ejecutivo
comercial), así que sin esto no hay forma de recorrer el despacho desde la
pantalla. Atiende con la misma Andreani simulada de los tests
(``andreani/tests/fake_andreani.py``): login, orden de envío, etiquetas,
trazas, sucursales, cotizador y cancelación. Nada sale a internet.

Para usarla:

1. ``ANDREANI_API_BASE_QA=http://127.0.0.1:8099`` en el ``.env`` (y reiniciar
   el backend).
2. Cargar la cuenta en ambiente QA con usuario ``cliente-prueba``, contraseña
   ``Clave-Andreani-1``, código de cliente cualquiera y el contrato
   ``400006709`` (domicilio) o ``400006710`` (sucursal).

Las etiquetas son un PDF que dice "PRUEBA". Cada vez que se consulta el
seguimiento de un envío, avanza un paso: admitido, en camino, entregado.
"""

import base64
import io
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.carriers.andreani.tests.fake_andreani import QA, FakeAndreani, event

# Lo que va mostrando el seguimiento simulado, un paso por consulta.
TRACKING_STEPS = (
    ("Admision", {}),
    ("EnvioDespachado", {}),
    ("EnvioEntregado", {"Motivo": "Entregado", "Estado": "Entregado"}),
)


def label_pdf(number):
    """Un PDF de verdad (el de los tests es un texto que ningún visor abre)."""
    from reportlab.lib.pagesizes import A6
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A6)
    width, height = A6
    page.setFont("Helvetica-Bold", 22)
    page.drawCentredString(width / 2, height - 60, "ANDREANI")
    page.setFont("Helvetica-Bold", 14)
    page.drawCentredString(width / 2, height - 90, "ETIQUETA DE PRUEBA")
    page.setFont("Helvetica", 11)
    page.drawCentredString(width / 2, height - 130, f"Envío {number}")
    page.drawCentredString(width / 2, 40, "Andreani simulada: no se despacha nada.")
    page.showPage()
    page.save()
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    fake = None

    def _respond(self, fake_response):
        content = fake_response.content
        if isinstance(content, bytes) and content.startswith(b"%PDF"):
            content = label_pdf(self.path.split("/")[3])
            content_type = "application/pdf"
        elif isinstance(content, bytes) and content:
            content_type = "application/zpl"
        else:
            content = json.dumps(fake_response.json()).encode()
            content_type = "application/json"
        self.send_response(fake_response.status_code)
        self.send_header("Content-Type", content_type)
        for name, value in (fake_response.headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _advance_tracking(self, path):
        number = path.split("/")[3]
        events = self.fake.traces.setdefault(number, [])
        if len(events) < len(TRACKING_STEPS):
            name, extra = TRACKING_STEPS[len(events)]
            events.append(event(name, timezone.localtime().strftime("%Y-%m-%dT%H:%M:%S.000"), **extra))

    def _handle(self, method):
        parsed = urlparse(self.path)
        if parsed.path == "/login":
            auth = ()
            header = self.headers.get("Authorization", "")
            if header.startswith("Basic "):
                auth = tuple(base64.b64decode(header[6:]).decode().split(":", 1))
            return self._respond(self.fake.get(f"{QA}/login", auth=auth))
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"null") if length else None
        params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        if method == "GET" and parsed.path.endswith("/trazas"):
            self._advance_tracking(parsed.path)
        try:
            result = self.fake.request(method, f"{QA}{parsed.path}", params=params, json=body, headers=self.headers)
        except AssertionError as exc:
            self.send_error(404, str(exc))
            return
        self._respond(result)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


class Command(BaseCommand):
    help = "Levanta una Andreani simulada en local para probar el despacho sin credenciales."

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=8099)

    def handle(self, *args, **options):
        Handler.fake = FakeAndreani()
        # Números que no se repiten entre arranques (la base guarda los envíos
        # de la vez anterior y el número de Andreani es único): "36" + la hora
        # en milisegundos, 15 dígitos como los reales.
        Handler.fake.next_number = int(f"36{int(time.time() * 1000):013d}")
        server = ThreadingHTTPServer(("127.0.0.1", options["port"]), Handler)
        self.stdout.write(
            self.style.SUCCESS(f"Andreani simulada en http://127.0.0.1:{options['port']} (Ctrl+C para cortar).")
        )
        self.stdout.write("Usuario: cliente-prueba · Contraseña: Clave-Andreani-1 · Contratos: 400006709 / 400006710")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
