"""Tests de la capa común de transportistas (``base.py``, ``views.py``,
``printing.py``, ``tracking.py``, ``push_views.py``): Andreani por las rutas
comunes, un PDF con envíos de distintos transportistas, el worker siguiendo a
todos y las novedades push de Andreani."""

import base64
import io
from unittest.mock import patch

from django.test import override_settings
from pypdf import PdfReader
from reportlab.pdfgen import canvas
from rest_framework.test import APITestCase

from apps.carriers.andreani.tests.fake_andreani import AndreaniTestMixin
from apps.carriers.andreani.tests.test_andreani import make_order
from apps.carriers.models import CarrierAccount, CarrierPushToken, CarrierShipment
from apps.carriers.oca.tests.fake_oca import OcaTestMixin
from apps.carriers.tracking import sync_due_shipments
from apps.integrations.tests.helpers import auth_headers_for, make_user
from apps.orders.models import Order
from apps.accounts.tests.team_helpers import give_solo_teams

API = "/api/v1/carriers"


def pdf_page(text):
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    page.drawString(72, 720, text)
    page.showPage()
    page.save()
    return buffer.getvalue()


# Las Andreani y OCA simuladas atienden las URL de QA por defecto, aunque el
# .env local apunte a la Andreani simulada de ``fake_andreani_server``.
@override_settings(ANDREANI_API_BASE_QA="https://apisqa.andreani.com")
class CommonApiTests(AndreaniTestMixin, OcaTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")
        give_solo_teams()  # comercio "solo" (team_helpers, transitorio)
        self.andreani_account = self.make_account(self.user)
        self.oca_account = self.make_oca_account(self.user)
        give_solo_teams()  # comercio "solo" (team_helpers, transitorio)

    def post(self, path, payload, user=None):
        return self.client.post(f"{API}{path}", payload, format="json", **auth_headers_for(user or self.user))

    def test_andreani_por_las_rutas_comunes(self):
        order = make_order(self.user)
        quote = self.post("/shipments/quote/", {"carrier": "andreani", "order_ids": [order.pk], "contract": "400006709"})
        self.assertEqual(quote.status_code, 200, quote.content)
        self.assertTrue(quote.json()["results"][0]["ok"])
        created = self.post("/shipments/", {"carrier": "andreani", "order_ids": [order.pk], "contract": "400006709"})
        result = created.json()["results"][0]
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["carrier"], "andreani")
        self.assertIn("andreani.com", result["tracking_url"])
        self.assertTrue(result["can_cancel"])

    def test_sin_transportista_o_sin_cuenta(self):
        order = make_order(self.user)
        self.assertEqual(self.post("/shipments/", {"order_ids": [order.pk]}).status_code, 400)
        other = make_user("otro@example.com")
        give_solo_teams()  # comercio "solo" (team_helpers, transitorio)
        response = self.post("/shipments/", {"carrier": "oca", "order_ids": [order.pk], "contract": "64665"}, user=other)
        self.assertEqual(response.status_code, 400)
        self.assertIn("OCA", response.json()["detail"])
        self.assertEqual(self.client.get(f"{API}/accounts/inexistente/", **auth_headers_for(self.user)).status_code, 404)

    def test_un_pedido_con_envio_abierto_no_se_despacha_con_otro_transportista(self):
        order = make_order(self.user)
        self.post("/shipments/", {"carrier": "andreani", "order_ids": [order.pk], "contract": "400006709"})
        again = self.post("/shipments/", {"carrier": "oca", "order_ids": [order.pk], "contract": "64665"})
        self.assertFalse(again.json()["results"][0]["ok"])
        self.assertIn("Andreani", again.json()["results"][0]["detail"])

    def test_la_lista_trae_los_envios_de_todos(self):
        first, second = make_order(self.user), make_order(self.user, recipient_name="Juan Pérez")
        self.post("/shipments/", {"carrier": "andreani", "order_ids": [first.pk], "contract": "400006709"})
        self.post("/shipments/", {"carrier": "oca", "order_ids": [second.pk], "contract": "64665"})
        listing = self.client.get(f"{API}/shipments/", **auth_headers_for(self.user)).json()["results"]
        self.assertEqual({item["carrier"] for item in listing}, {"andreani", "oca"})
        only = self.client.get(f"{API}/shipments/?carrier=oca", **auth_headers_for(self.user)).json()["results"]
        self.assertEqual([item["carrier"] for item in only], ["oca"])

    def test_un_pdf_con_envios_de_distintos_transportistas(self):
        first, second = make_order(self.user), make_order(self.user, recipient_name="Juan Pérez")
        self.post("/shipments/", {"carrier": "andreani", "order_ids": [first.pk], "contract": "400006709"})
        self.post("/shipments/", {"carrier": "oca", "order_ids": [second.pk], "contract": "64665"})
        ids = list(CarrierShipment.objects.order_by("pk").values_list("pk", flat=True))
        with (
            patch("apps.carriers.andreani.client.AndreaniClient.label", return_value=pdf_page("ANDREANI")),
            patch("apps.carriers.oca.client.OcaClient.label", return_value=pdf_page("OCA")),
        ):
            response = self.post("/shipments/print/", {"shipment_ids": ids})
        self.assertEqual(response.status_code, 200, response.content)
        texts = [page.extract_text() for page in PdfReader(io.BytesIO(response.content)).pages]
        self.assertIn("ANDREANI", texts[1])
        self.assertIn("OCA", texts[-1])

    def test_el_worker_sigue_a_todos_los_transportistas(self):
        first, second = make_order(self.user), make_order(self.user, recipient_name="Juan Pérez")
        self.post("/shipments/", {"carrier": "andreani", "order_ids": [first.pk], "contract": "400006709"})
        self.post("/shipments/", {"carrier": "oca", "order_ids": [second.pk], "contract": "64665"})
        oca_shipment = CarrierShipment.objects.get(carrier="oca")
        self.oca.traces[oca_shipment.tracking_number] = [{"Estado": "Retirado", "IdEstado": "3", "Fecha": "2026-10-15T10:00:00-03:00"}]
        self.assertEqual(sync_due_shipments(), 1)
        self.assertTrue(self.oca.calls_to("Tracking_Pieza_ConIdEstado"))
        self.assertTrue(self.andreani.calls_to("GET", f"/v3/envios/{CarrierShipment.objects.get(carrier='andreani').tracking_number}/trazas"))
        second.refresh_from_db()
        self.assertEqual(second.status, Order.Status.DISPATCHED)

    def test_credenciales_rechazadas_en_el_seguimiento_desactivan_esa_cuenta(self):
        order = make_order(self.user)
        self.post("/shipments/", {"carrier": "oca", "order_ids": [order.pk], "contract": "64665"})
        from apps.carriers.oca.client import OcaAuthError

        with patch("apps.carriers.oca.client.OcaClient.traces", side_effect=OcaAuthError("OCA rechazó el usuario.")):
            sync_due_shipments()
        self.oca_account.refresh_from_db()
        self.andreani_account.refresh_from_db()
        self.assertFalse(self.oca_account.is_active)
        self.assertTrue(self.andreani_account.is_active)


@override_settings(INTEGRATIONS_PUBLIC_BASE_URL="https://app.example.com", ANDREANI_API_BASE_QA="https://apisqa.andreani.com")
class AndreaniPushTests(AndreaniTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")
        give_solo_teams()  # comercio "solo" (team_helpers, transitorio)
        self.account = self.make_account(self.user)
        self.order = make_order(self.user)
        created = self.client.post(
            f"{API}/shipments/", {"carrier": "andreani", "order_ids": [self.order.pk], "contract": "400006709"}, format="json", **auth_headers_for(self.user)
        )
        self.shipment = CarrierShipment.objects.get(pk=created.json()["results"][0]["id"])
        generated = self.client.post(f"{API}/accounts/andreani/push/", **auth_headers_for(self.user))
        self.assertEqual(generated.status_code, 201, generated.content)
        self.credentials = generated.json()
        give_solo_teams()  # comercio "solo" (team_helpers, transitorio)

    def push(self, payload, username=None, password=None, url=None):
        raw = f"{username or self.credentials['username']}:{password or self.credentials['password']}".encode()
        return self.client.post(
            url or self.credentials["url"].replace("https://app.example.com", ""),
            payload,
            format="json",
            HTTP_AUTHORIZATION="Basic " + base64.b64encode(raw).decode(),
        )

    def novedad(self, estado, fecha, ciclo="Distribution", **extra):
        return {
            "IdCliente": "1001",
            "NumeroDeEnvio": self.shipment.tracking_number,
            "Contrato": "400006709",
            "Estado": estado,
            "FechaEstado": fecha,
            "Motivo": "",
            "SubMotivo": "",
            "Ciclo": ciclo,
            **extra,
        }

    def test_la_contraseña_se_muestra_una_vez_y_se_guarda_como_hash(self):
        self.assertTrue(self.credentials["url"].startswith("https://app.example.com/api/v1/carriers/push/andreani/"))
        token = CarrierPushToken.objects.get()
        self.assertNotIn(self.credentials["password"], token.password_hash)
        again = self.client.get(f"{API}/accounts/andreani/push/", **auth_headers_for(self.user))
        self.assertNotIn("password", again.json())
        self.assertTrue(again.json()["configured"])

    def test_novedades_mueven_envio_y_pedido(self):
        response = self.push([self.novedad("Admision", "2026-10-15T09:00:00.0000000-03:00"), self.novedad("EnvioEntregado", "2026-10-16T12:30:01.4980000-03:00")])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "OK")
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, CarrierShipment.Status.DELIVERED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.DELIVERED)
        self.assertEqual(CarrierPushToken.objects.get().received_count, 2)

    def test_entregado_en_la_devolucion_no_es_entregado(self):
        self.push(self.novedad("EnvioEntregado", "2026-10-16T12:30:00-03:00", ciclo="Devolucion"))
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, CarrierShipment.Status.RETURNING)

    def test_credenciales_malas_y_envio_ajeno(self):
        self.assertEqual(self.push(self.novedad("Admision", "2026-10-15T09:00:00-03:00"), password="otra").status_code, 401)
        other_user = make_user("otro@example.com")
        give_solo_teams()  # comercio "solo" (team_helpers, transitorio)
        other_account = self.make_account(other_user)
        foreign = CarrierShipment.objects.create(
            account=other_account, order=make_order(other_user), carrier="andreani", contract="1", tracking_number="999"
        )
        response = self.push(self.novedad("EnvioEntregado", "2026-10-16T12:30:00-03:00", NumeroDeEnvio=foreign.tracking_number))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ERROR")
        foreign.refresh_from_db()
        self.assertEqual(foreign.status, CarrierShipment.Status.PENDING)

    def test_regenerar_invalida_las_anteriores(self):
        old = dict(self.credentials)
        self.client.post(f"{API}/accounts/andreani/push/", **auth_headers_for(self.user))
        response = self.push(self.novedad("Admision", "2026-10-15T09:00:00-03:00"), url=old["url"].replace("https://app.example.com", ""))
        self.assertEqual(response.status_code, 401)

    def test_otro_transportista_no_tiene_push(self):
        self.assertEqual(self.client.post(f"{API}/accounts/oca/push/", **auth_headers_for(self.user)).status_code, 404)
        self.assertTrue(CarrierAccount.objects.filter(carrier="andreani").exists())
