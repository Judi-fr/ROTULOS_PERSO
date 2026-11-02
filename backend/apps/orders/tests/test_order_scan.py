"""Escanear para despachar: GET /orders/scan/?code= encuentra el pedido por lo
que trae el rótulo (código de barras o QR), solo dentro del comercio actual."""

from urllib.parse import parse_qs, urlparse

from rest_framework.test import APITestCase

from apps.accounts import teams
from apps.accounts.tests.test_team_permissions import bearer, make_owner
from apps.integrations.models import StoreConnection
from apps.labels.label_rendering import build_label_context
from apps.orders.models import Address, Order

URL = "/api/v1/orders/scan/"


class OrderScanTests(APITestCase):
    def setUp(self):
        self.owner = make_owner("dueno@example.com")
        self.biz = teams.data_owner(self.owner)
        self.store_a = StoreConnection.objects.create(
            owner=self.biz, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="a", name="A"
        )
        self.store_b = StoreConnection.objects.create(
            owner=self.biz, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="b", name="B"
        )
        self.address = Address.objects.create(user=self.biz, recipient_name="Ana", street="Calle", number="1", city="CABA")
        self.plain = Order.objects.create(user=self.biz, address=self.address)
        self.tracked = Order.objects.create(
            user=self.biz, address=self.address, store_connection=self.store_a, external_id="t1",
            external_number="1001", tracking_number="361793323593504",
            tracking_url="https://www.andreani.com/envio/361793323593504",
        )
        other_biz = teams.data_owner(make_owner("otro@example.com"))
        other_address = Address.objects.create(user=other_biz, recipient_name="B", street="X", number="1", city="Y")
        self.foreign = Order.objects.create(user=other_biz, address=other_address, tracking_number="999000999")

    def scan(self, code):
        response = self.client.get(URL, {"code": code}, **bearer(self.owner))
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return [row["id"] for row in response.data["matches"]]

    def test_por_numero_de_seguimiento_del_transportista(self):
        self.assertEqual(self.scan("361793323593504"), [self.tracked.pk])

    def test_por_el_codigo_de_barras_de_un_pedido_sin_seguimiento(self):
        # Sin seguimiento real el rótulo imprime el id con ceros a la izquierda.
        code = build_label_context(order=self.plain)["tracking"]
        self.assertTrue(code.startswith("0"))
        self.assertEqual(self.scan(code), [self.plain.pk])

    def test_por_el_qr_de_un_pedido_sin_seguimiento(self):
        self.assertEqual(self.scan(build_label_context(order=self.plain)["tracking_url"]), [self.plain.pk])

    def test_por_el_qr_con_el_link_del_transportista(self):
        self.assertEqual(self.scan("https://www.andreani.com/envio/361793323593504"), [self.tracked.pk])

    def test_por_el_numero_que_ve_el_comprador(self):
        self.assertEqual(self.scan("#1001"), [self.tracked.pk])

    def test_dos_tiendas_con_el_mismo_numero_devuelven_los_dos(self):
        twin = Order.objects.create(
            user=self.biz, address=self.address, store_connection=self.store_b, external_id="t2", external_number="1001"
        )
        self.assertEqual(sorted(self.scan("1001")), sorted([self.tracked.pk, twin.pk]))

    def test_no_encuentra_pedidos_de_otro_comercio(self):
        self.assertEqual(self.scan("999000999"), [])
        self.assertEqual(self.scan(f"{self.foreign.pk:06d}"), [])

    def test_un_link_firmado_de_otro_comercio_no_sirve(self):
        url = build_label_context(order=self.foreign)["tracking_url"]
        self.assertIn("t=", url)
        self.assertEqual(self.scan(url), [])

    def test_sin_codigo_es_400(self):
        response = self.client.get(URL, {"code": "  "}, **bearer(self.owner))
        self.assertEqual(response.status_code, 400)

    def test_el_qr_lleva_un_link_firmado_y_no_el_id(self):
        url = build_label_context(order=self.plain)["tracking_url"]
        query = parse_qs(urlparse(url).query)
        self.assertNotIn(str(self.plain.pk), query["t"][0].split(":")[0])
