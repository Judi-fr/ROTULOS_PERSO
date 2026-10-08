"""Tests de Magento (fase 1): dirección de la tienda, firma OAuth 1.0a,
conexión con las credenciales de una Integración, importación y repaso por
fecha de modificación, y el despacho como envío con seguimiento. El Magento
simulado está en ``fake_magento.py``."""

import base64
import hashlib
import hmac
import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.integrations.events import process_due_events
from apps.integrations.models import IntegrationEvent, StoreConnection
from apps.integrations.providers import get_provider
from apps.integrations.providers.magento.oauth import oauth1_header
from apps.integrations.stores import RECONCILE_ORDERS_EVENT, enqueue_due_reconciliations, enqueue_store_setup
from apps.integrations.tests.helpers import auth_headers_for, make_user
from apps.orders.ingestion import upsert_store_order
from apps.orders.models import Order

from .fake_magento import CREDS, EXTERNAL_ID, MAGENTO_SETTINGS, SITE, MagentoTestMixin, _encode, magento_order


class SiteUrlTests(TestCase):
    def setUp(self):
        self.provider = get_provider("magento")

    def test_acepta_lo_que_pegaria_un_comerciante(self):
        cases = {
            "mitienda.com.ar": SITE,
            "https://MiTienda.com.ar/": SITE,
            "https://mitienda.com.ar/index.php/rest/V1/orders": SITE,
            "https://mitienda.com.ar/tienda/rest/V1": f"{SITE}/tienda",
        }
        for value, expected in cases.items():
            self.assertEqual(self.provider.normalize_shop_domain(value), expected, value)

    def test_rechaza_http_y_cualquier_cosa(self):
        for value in ("http://mitienda.com.ar", "", "mitienda", "con espacios.com"):
            with self.assertRaises(ValueError, msg=value):
                self.provider.normalize_shop_domain(value)


class OAuthSignatureTests(TestCase):
    def test_firma_hmac_sha256_reproducible(self):
        header = oauth1_header(
            "GET",
            "https://mitienda.com.ar/rest/V1/orders",
            {"searchCriteria[pageSize]": "1"},
            "ck",
            "cs",
            "at",
            "ats",
            nonce="abc",
            timestamp=1700000000,
        )
        base = "&".join(
            (
                "GET",
                _encode("https://mitienda.com.ar/rest/V1/orders"),
                _encode(
                    "oauth_consumer_key=ck&oauth_nonce=abc&oauth_signature_method=HMAC-SHA256&oauth_timestamp=1700000000"
                    "&oauth_token=at&oauth_version=1.0&searchCriteria%5BpageSize%5D=1"
                ),
            )
        )
        expected = base64.b64encode(hmac.new(b"cs&ats", base.encode(), hashlib.sha256).digest()).decode()
        self.assertIn(f'oauth_signature="{_encode(expected)}"', header)
        self.assertTrue(header.startswith("OAuth "))


class NormalizeOrderTests(TestCase):
    def setUp(self):
        self.provider = get_provider("magento")

    def test_traduce_lo_que_necesita_el_rotulo(self):
        normalized = self.provider.normalize_order(magento_order(55))

        self.assertEqual((normalized.external_id, normalized.external_number), ("55", "000000055"))
        self.assertEqual(normalized.recipient_name, "María Gómez")
        self.assertEqual((normalized.street, normalized.number, normalized.reference), ("Av. Colón", "1234", "Piso 3 B"))
        self.assertEqual((normalized.city, normalized.state, normalized.postal_code), ("Córdoba", "Córdoba", "5000"))
        self.assertEqual(normalized.country, "Argentina")
        self.assertEqual(normalized.shipping_option, "Envío a domicilio - Estándar")
        # La variante de un configurable no se cuenta dos veces.
        self.assertEqual(normalized.description, "2x Remera")
        self.assertEqual(normalized.status, "")
        self.assertEqual(normalized.external_updated_at.isoformat(), "2026-10-01T12:00:00+00:00")
        stored = json.dumps(normalized.raw)
        for secret in ("ana@example.com", "351444000", "4111"):
            self.assertNotIn(secret, stored)

    def test_sin_envio_usa_la_de_facturacion_y_codigo_de_provincia(self):
        billing = dict(magento_order(1)["billing_address"], region="", region_code="C")
        normalized = self.provider.normalize_order(magento_order(1, extension_attributes={}, billing_address=billing))
        self.assertEqual(normalized.recipient_name, "Ana López")
        self.assertEqual((normalized.street, normalized.number), ("Rivadavia", "100"))
        self.assertEqual(normalized.state, "Ciudad Autónoma de Buenos Aires")

    def test_estados(self):
        for state, local in (("canceled", "cancelled"), ("closed", "cancelled"), ("complete", "dispatched"), ("processing", ""), ("holded", "")):
            self.assertEqual(self.provider.normalize_order(magento_order(1, state=state)).status, local, state)


@override_settings(**MAGENTO_SETTINGS)
class ManualConnectTests(MagentoTestMixin, APITestCase):
    URL = "/api/v1/integrations/magento/connect-manual/"

    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")

    def _connect(self, **data):
        payload = dict(CREDS, site_url="mitienda.com.ar")
        payload.update(data)
        return self.client.post(self.URL, payload, format="json", **auth_headers_for(self.user))

    def test_credenciales_validas_conectan_y_lanzan_la_configuracion(self):
        response = self._connect()

        self.assertEqual(response.status_code, 201, response.data)
        connection = StoreConnection.objects.get(pk=response.data["id"])
        self.assertEqual((connection.platform, connection.external_store_id, connection.store_url), ("magento", EXTERNAL_ID, SITE))
        self.assertEqual(json.loads(connection.access_token), CREDS)
        self.assertEqual(connection.preferences["magento_rest_path"], "/rest/V1/")
        self.assertTrue(IntegrationEvent.objects.filter(connection=connection, event_type="internal/store_setup").exists())

    def test_hosting_sin_reescritura_usa_index_php(self):
        self.magento.rest_path = "/index.php/rest/V1/"

        response = self._connect()

        self.assertEqual(response.status_code, 201, response.data)
        connection = StoreConnection.objects.get(pk=response.data["id"])
        self.assertEqual(connection.preferences["magento_rest_path"], "/index.php/rest/V1/")
        # Las llamadas siguientes van por la misma ruta.
        self.magento.orders = {7: magento_order(7)}
        self.assertEqual(get_provider("magento").get_order(connection, 7)["entity_id"], 7)

    def test_credenciales_rechazadas(self):
        response = self._connect(access_token_secret="otro")
        self.assertEqual(response.status_code, 400)
        self.assertIn("401", response.data["detail"])
        self.assertFalse(StoreConnection.objects.exists())

    def test_sin_magento_en_esa_direccion(self):
        self.magento.rest_path = "/no-esta/"
        response = self._connect()
        self.assertEqual(response.status_code, 400)
        self.assertIn("No encontramos la API de Magento", response.data["detail"])

    def test_faltan_credenciales_o_http(self):
        self.assertEqual(self._connect(access_token="").status_code, 400)
        self.assertEqual(self._connect(site_url="http://mitienda.com.ar").status_code, 400)
        self.assertEqual(self.magento.calls, [])

    def test_no_hay_url_de_instalacion(self):
        response = self.client.get("/api/v1/integrations/magento/install-url/", **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 404)


@override_settings(**MAGENTO_SETTINGS)
class ImportAndReconcileTests(MagentoTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.connection = self._connection(owner=make_user("comercio@example.com"))

    def test_configurar_importa_todas_las_paginas_aunque_magento_repita_la_ultima(self):
        self.magento.orders = {i: magento_order(i, created_at=f"2026-10-0{i} 10:00:00") for i in (1, 2, 3)}
        old = magento_order(4, created_at="2020-01-01 10:00:00")
        self.magento.orders[4] = old

        enqueue_store_setup(self.connection)
        for _ in range(4):
            process_due_events()

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.name, EXTERNAL_ID)
        # Sin webhooks no hace falta la URL pública: no queda ningún aviso.
        self.assertEqual(self.connection.last_error, "")
        self.assertEqual(sorted(Order.objects.values_list("external_number", flat=True)), ["00000001", "00000002", "00000003"])
        self.assertEqual(len(self.magento.calls_to("GET", "orders")), 2)
        self.assertFalse(IntegrationEvent.objects.exclude(status=IntegrationEvent.Status.DONE).exists())

    def test_el_repaso_trae_los_modificados_con_la_fecha_de_magento(self):
        now = timezone.now()
        self.magento.orders = {5: magento_order(5, state="canceled", updated_at="2099-01-01 00:00:00")}

        self.assertEqual(enqueue_due_reconciliations(now), 1)
        self.assertEqual(enqueue_due_reconciliations(now + timedelta(minutes=4)), 0)
        process_due_events()

        query = self.magento.calls_to("GET", "orders")[0]["query"]
        self.assertEqual(query["searchCriteria[filter_groups][0][filters][0][field]"], "updated_at")
        self.assertRegex(query["searchCriteria[filter_groups][0][filters][0][value]"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertEqual(Order.objects.get(external_id="5").status, Order.Status.CANCELLED)
        self.assertTrue(IntegrationEvent.objects.filter(event_type=RECONCILE_ORDERS_EVENT, status="done").exists())


@override_settings(**MAGENTO_SETTINGS)
class PushFulfillmentTests(MagentoTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("comercio@example.com")
        self.connection = self._connection(owner=self.owner)
        self.magento.orders = {90: magento_order(90)}
        order, _ = upsert_store_order(self.connection, get_provider("magento").normalize_order(magento_order(90)))
        self.order = Order.objects.get(pk=order.pk)

    def _ship(self, **data):
        data.setdefault("status", "dispatched")
        response = self.client.post(f"/api/v1/orders/{self.order.pk}/ship/", data, format="json", **auth_headers_for(self.owner))
        self.assertEqual(response.status_code, 200, response.data)
        process_due_events()

    def test_despachar_crea_el_envio_con_el_seguimiento(self):
        self._ship(carrier="Correo Argentino", tracking_number="AR123", tracking_url="https://seguimiento.example.com/AR123")

        shipment = self.magento.shipments[0]
        self.assertEqual(shipment["order_id"], 90)
        self.assertTrue(shipment["notified"])
        self.assertEqual(shipment["tracks"], [{"track_number": "AR123", "title": "Correo Argentino", "carrier_code": "custom"}])
        comment = self.magento.orders[90]["status_histories"][0]
        self.assertIn("https://seguimiento.example.com/AR123", comment["comment"])
        self.assertEqual(comment["is_visible_on_front"], 1)

    def test_si_ya_lo_despacho_desde_magento_solo_agrega_el_seguimiento(self):
        self.magento.shipments = [{"entity_id": 7, "order_id": 90, "tracks": []}]

        self._ship(tracking_number="AR123")

        self.assertEqual(len(self.magento.shipments), 1)
        self.assertEqual(self.magento.shipments[0]["tracks"][0]["track_number"], "AR123")
        self.assertEqual(len(self.magento.calls_to("POST", "shipment/7/emails")), 1)

    def test_el_mismo_seguimiento_no_se_repite(self):
        self.magento.shipments = [{"entity_id": 7, "order_id": 90, "tracks": [{"track_number": "AR123"}]}]
        self._ship(tracking_number="AR123")
        self.assertEqual(self.magento.calls_to("POST", "shipment/track"), [])
        self.assertEqual(self.magento.calls_to("POST", "order/90/ship"), [])

    def test_pedido_cancelado_en_magento_no_se_toca(self):
        self.magento.orders[90]["state"] = "canceled"
        self._ship(tracking_number="AR123")
        self.assertEqual([call for call in self.magento.calls if call["method"] != "GET"], [])
