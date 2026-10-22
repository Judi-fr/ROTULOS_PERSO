"""``python manage.py fake_correo_server`` — un Correo Argentino simulado en
``http://127.0.0.1:8098`` para probar a mano, SOLO en local.

Correo da las credenciales de Paq.ar y MiCorreo únicamente a clientes con
convenio, así que sin esto no hay forma de recorrer el despacho desde la
pantalla. Atiende con el mismo Correo simulado de los tests
(``correo_argentino/tests/fake_correo.py``): Paq.ar (alta del pedido,
etiquetas, seguimiento, cancelación y sucursales) y MiCorreo (token y
cotización). Nada sale a internet.

Para usarlo:

1. En el ``.env`` (y reiniciar el backend)::

       CORREO_ARGENTINO_PAQAR_BASE_QA=http://127.0.0.1:8098/paqar/v1
       CORREO_ARGENTINO_MICORREO_BASE_QA=http://127.0.0.1:8098/micorreo/v1

2. En ``transportista.html?carrier=correo_argentino``, ambiente de prueba (QA):
   acuerdo ``18017``, API key ``clave-paqar``, servicio ``CP``; para cotizar,
   usuario de MiCorreo ``api-user``, contraseña ``api-pass`` y número de cliente
   ``0090000025``. Además, los datos de origen (calle, CP, ciudad, provincia).

Las etiquetas son un PDF que dice "PRUEBA". Cada vez que se consulta el
seguimiento de un envío avanza un paso: impuesto (despachado), en tránsito,
entregado.
"""

import base64
import io
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.carriers.correo_argentino.tests.fake_correo import (
    AGREEMENT,
    API_KEY,
    CUSTOMER_ID,
    HOST,
    MICORREO_PASSWORD,
    MICORREO_USER,
    FakeCorreo,
)

# Lo que va mostrando el seguimiento simulado, un paso por consulta.
TRACKING_STEPS = (
    ("IMP", "IMPOSICION", "CTP MONTE GRANDE"),
    ("TRN", "EN TRANSITO A SUCURSAL DE DESTINO", "CTP BENAVIDEZ"),
    ("ENT", "ENTREGADO", "SUCURSAL DE DESTINO"),
)


def label_pdf(number):
    """Un PDF de verdad (el de los tests es un texto que ningún visor abre)."""
    from reportlab.lib.pagesizes import A6
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=A6)
    width, height = A6
    page.setFont("Helvetica-Bold", 18)
    page.drawCentredString(width / 2, height - 60, "CORREO ARGENTINO")
    page.setFont("Helvetica-Bold", 14)
    page.drawCentredString(width / 2, height - 90, "ETIQUETA DE PRUEBA")
    page.setFont("Helvetica", 10)
    page.drawCentredString(width / 2, height - 130, f"Envío {number}")
    page.drawCentredString(width / 2, 40, "Correo simulado: no se despacha nada.")
    page.showPage()
    page.save()
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    fake = None

    def _respond(self, fake_response):
        self.send_response(fake_response.status_code)
        if fake_response.status_code == 204:
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        content = json.dumps(fake_response.json()).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _advance_tracking(self, body):
        for item in body if isinstance(body, list) else []:
            number = str(item.get("trackingNumber") or "")
            events = self.fake.tracking.setdefault(number, [])
            if number and len(events) < len(TRACKING_STEPS):
                status_id, status, facility = TRACKING_STEPS[len(events)]
                events.append(
                    {"statusId": status_id, "status": status, "facility": facility, "date": timezone.localtime().isoformat()}
                )

    def _handle(self, method):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"null") if length else None
        params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        # El Correo de los tests contesta en el host de prueba real.
        url = f"{HOST}{parsed.path}"
        try:
            if parsed.path.startswith("/micorreo/v1/") and method == "POST":
                auth = ()
                header = self.headers.get("Authorization", "")
                if header.startswith("Basic "):
                    auth = tuple(base64.b64decode(header[6:]).decode().split(":", 1))
                result = self.fake.post(url, json=body, auth=auth, headers=self.headers)
            elif parsed.path.startswith("/paqar/v1/"):
                if method == "GET" and parsed.path.endswith("/tracking"):
                    self._advance_tracking(body)
                result = self.fake.request(method, url, params=params, json=body, headers=self.headers)
                if method == "POST" and parsed.path.endswith("/labels") and result.status_code == 200:
                    labels = result.json()
                    for label in labels:
                        label["fileBase64"] = base64.b64encode(label_pdf(label["trackingNumber"])).decode()
                    result.json.return_value = labels
            else:
                raise AssertionError(f"Ruta no simulada: {parsed.path}")
        except AssertionError as exc:
            self.send_error(404, str(exc))
            return
        self._respond(result)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")


class Command(BaseCommand):
    help = "Levanta un Correo Argentino simulado en local para probar el despacho sin credenciales."

    def add_arguments(self, parser):
        parser.add_argument("--port", type=int, default=8098)

    def handle(self, *args, **options):
        Handler.fake = FakeCorreo()
        # Números que no se repiten entre arranques: la base guarda los envíos
        # de la vez anterior y el número del Correo es único.
        Handler.fake.next_number = int(time.time())
        port = options["port"]
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.stdout.write(self.style.SUCCESS(f"Correo Argentino simulado en http://127.0.0.1:{port} (Ctrl+C para cortar)."))
        self.stdout.write(f"En el .env: CORREO_ARGENTINO_PAQAR_BASE_QA=http://127.0.0.1:{port}/paqar/v1")
        self.stdout.write(f"            CORREO_ARGENTINO_MICORREO_BASE_QA=http://127.0.0.1:{port}/micorreo/v1")
        self.stdout.write(
            f"Paq.ar: acuerdo {AGREEMENT} · API key {API_KEY} · servicio CP   "
            f"MiCorreo: {MICORREO_USER} / {MICORREO_PASSWORD} · cliente {CUSTOMER_ID}"
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
