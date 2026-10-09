"""Acciones masivas desde el admin de la tienda (``store_print.action_link``):
además de imprimir rótulos, la planilla de retiro y despachar con Andreani.
Se prueban por el plugin de WooCommerce (firma con el secreto de la tienda) y
por el link de Tiendanube (sesión de la app); Shopify usa la misma función.

Andreani se simula a nivel de su cliente y no de ``requests``: la WooCommerce
simulada ya parchea ``requests`` y las dos no pueden convivir."""

import base64
import hashlib
import hmac
import io
import json
import time
from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings
from pypdf import PdfReader
from reportlab.pdfgen import canvas
from rest_framework.test import APITestCase

from apps.carriers.andreani.tests.fake_andreani import AndreaniTestMixin
from apps.carriers.models import CarrierShipment
from apps.integrations import store_print
from apps.integrations.providers import get_provider
from apps.integrations.providers.woocommerce.tests.test_woocommerce import BASE_URL, WOO_SETTINGS, WooTestMixin, woo_order
from apps.integrations.tests.helpers import make_user
from apps.orders.models import Order

PRINT_LINK_URL = "/api/v1/integrations/woocommerce/print-link/"


def andreani_label(*_args, **_kwargs):
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    page.drawString(72, 720, "ETIQUETA ANDREANI")
    page.showPage()
    page.save()
    return buffer.getvalue()


class FakeAndreaniClient:
    """Lo que ``create_shipment`` y ``printing`` le piden a Andreani."""

    def __init__(self):
        self.created = []
        self.next_number = 360000000000100

    def create_order(self, client, payload):
        self.created.append(payload)
        self.next_number += 1
        return {"estado": "Pendiente", "bultos": [{"numeroDeEnvio": str(self.next_number)}], "agrupadorDeBultos": f"G{self.next_number}"}


@override_settings(**WOO_SETTINGS)
class StoreActionTests(WooTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("comercio@example.com")
        self.connection = self._connection(owner=self.owner)
        self.secret = get_provider("woocommerce").ensure_webhook_secret(self.connection)
        self.woo.orders = {101: woo_order(101), 102: woo_order(102)}
        self.andreani = FakeAndreaniClient()
        for target, kwargs in (
            ("apps.carriers.andreani.client.AndreaniClient.create_order", {"autospec": True, "side_effect": self.andreani.create_order}),
            ("apps.carriers.andreani.client.AndreaniClient.label", {"side_effect": andreani_label}),
            ("apps.carriers.andreani.shipments.quote_order", {"return_value": {"price": Decimal("1210")}}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _link(self, ids, action=None):
        data = {"store": str(self.connection.pk), "ids": ids, "ts": int(time.time())}
        if action:
            data["action"] = action
        body = json.dumps(data).encode()
        signature = base64.b64encode(hmac.new(self.secret.encode(), body, hashlib.sha256).digest()).decode()
        return self.client.generic("POST", PRINT_LINK_URL, body, content_type="application/json", HTTP_X_ROTULOS_SIGNATURE=signature)

    def _document(self, response):
        document = self.client.get(response.data["url"].replace(BASE_URL, ""))
        self.assertEqual(document.status_code, 200, document.content[:200])
        self.assertEqual(document["Content-Type"], "application/pdf")
        return document

    def _pages(self, document):
        return [page.extract_text() for page in PdfReader(io.BytesIO(document.content)).pages]

    def test_sin_accion_sigue_imprimiendo_los_rotulos(self):
        response = self._link([101])

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["action"], "labels")
        self.assertIn("rotulos.pdf", self._document(response)["Content-Disposition"])

    def test_planilla_de_retiro(self):
        response = self._link([101, 102], "manifest")

        self.assertEqual(response.status_code, 200, response.data)
        document = self._document(response)
        self.assertIn("planilla-retiro.pdf", document["Content-Disposition"])
        self.assertIn("101", "".join(self._pages(document)))
        # La planilla no cambia nada.
        self.assertFalse(Order.objects.exclude(status=Order.Status.CREATED).exists())

    def test_despachar_crea_los_envios_y_baja_rotulo_y_etiqueta(self):
        AndreaniTestMixin.make_account(self, self.owner)

        response = self._link([102, 101], "dispatch")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["created"], response.data["failed"]), (2, []))
        self.assertEqual(len(self.andreani.created), 2)
        order = Order.objects.get(store_connection=self.connection, external_id="102")
        self.assertEqual(order.carrier, "Andreani")
        self.assertTrue(order.tracking_number.startswith("36"))
        self.assertEqual(order.status, Order.Status.PREPARING)
        # Contrato a domicilio: el de sucursal necesitaría elegir la sucursal.
        self.assertEqual({payload["contrato"] for payload in self.andreani.created}, {"400006709"})
        pages = self._pages(self._document(response))
        self.assertEqual(len(pages), 4)
        self.assertIn("ETIQUETA ANDREANI", pages[1])

    def test_despachar_usa_el_contrato_del_checkout_y_no_repite_envios(self):
        AndreaniTestMixin.make_account(
            self,
            self.owner,
            contracts=[{"code": "111", "label": "Domicilio A", "kind": "home"}, {"code": "222", "label": "Domicilio B", "kind": "home"}],
        )
        self.connection.preferences = {"andreani_checkout": {"enabled": True, "contract": "222"}}
        self.connection.save(update_fields=["preferences"])

        first = self._link([101], "dispatch")
        second = self._link([101], "dispatch")

        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(len(self.andreani.created), 1)
        self.assertEqual(self.andreani.created[0]["contrato"], "222")
        self.assertEqual(CarrierShipment.objects.count(), 1)

    def test_despachar_sin_cuenta_de_andreani_explica_que_hacer(self):
        response = self._link([101], "dispatch")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Conectá tu cuenta de Andreani", response.data["detail"])
        self.assertFalse(CarrierShipment.objects.exists())

    def test_despachar_tiene_un_tope_por_tanda(self):
        AndreaniTestMixin.make_account(self, self.owner)
        ids = list(range(1, store_print.MAX_ORDERS_PER_DISPATCH + 2))
        self.woo.orders = {order_id: woo_order(order_id) for order_id in ids}

        response = self._link(ids, "dispatch")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.andreani.created)

    def test_accion_desconocida(self):
        response = self._link([101], "borrar")

        self.assertEqual(response.status_code, 400)


@override_settings(INTEGRATIONS_PUBLIC_BASE_URL="https://rotulos.example.com")
class TiendanubeStoreActionTests(APITestCase):
    """El link de Tiendanube: la sesión de la app manda, y la acción viaja en
    el pedido (cada página del link la manda: planilla_tiendanube.html...)."""

    def setUp(self):
        from apps.integrations.models import StoreConnection
        from apps.integrations.tests.test_store_integration_base import tiendanube_order

        self.owner = make_user("comercio@example.com")
        StoreConnection.objects.create(platform="tiendanube", external_store_id="989346", owner=self.owner, access_token="tok")
        remote = {"501": tiendanube_order(id=501, number=1501)}
        patcher = patch(
            "apps.integrations.providers.tiendanube.provider.TiendanubeProvider.get_order",
            side_effect=lambda connection, order_id: remote[str(order_id)],
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_planilla_desde_el_link_de_tiendanube(self):
        from apps.integrations.tests.helpers import auth_headers_for

        response = self.client.post(
            "/api/v1/integrations/tiendanube/print-link/",
            {"store": "989346", "ids": ["501"], "action": "manifest"},
            format="json",
            **auth_headers_for(self.owner),
        )

        self.assertEqual(response.status_code, 200, response.data)
        document = self.client.get(response.data["url"].replace("https://rotulos.example.com", ""))
        self.assertEqual(document.status_code, 200)
        self.assertIn("planilla-retiro.pdf", document["Content-Disposition"])
