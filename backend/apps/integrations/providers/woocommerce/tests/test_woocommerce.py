"""Tests de WooCommerce: dirección del sitio, conexión automática (wc-auth) y
manual, webhooks firmados por tienda (y su "ping"), pedidos, despacho con
nota de seguimiento y el repaso periódico. La tienda se simula (``FakeWoo``):
nunca sale un pedido real."""

import base64
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.core import signing
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.orders.models import Order

from apps.integrations.events import process_due_events
from apps.integrations.models import IntegrationEvent, StoreConnection
from apps.integrations.providers import get_provider
from apps.integrations.providers.woocommerce.provider import credentials_token
from apps.integrations.stores import (
    IMPORT_ORDERS_EVENT,
    RECONCILE_ORDERS_EVENT,
    STATE_SALT,
    enqueue_due_reconciliations,
    enqueue_store_setup,
    make_oauth_state,
)
from apps.integrations.tests.helpers import auth_headers_for, fake_response, make_user

SITE = "https://mitienda.com.ar"
EXTERNAL_ID = "mitienda.com.ar"
BASE_URL = "https://rotulos.example.com"
WEBHOOKS_URL = "/api/v1/integrations/woocommerce/webhooks/"
FRONTEND = "http://localhost:8001"

WOO_SETTINGS = {
    "INTEGRATIONS_PUBLIC_BASE_URL": BASE_URL,
    "FRONTEND_URL": FRONTEND,
    "STORE_CONNECT_FRONTEND_PATH": "tiendas.html",
    "WOOCOMMERCE_APP_NAME": "Rotulos perso",
    "WOOCOMMERCE_ORDERS_PAGE_SIZE": 2,
}


def woo_order(order_id, **overrides):
    """Pedido con la forma de ``GET /wp-json/wc/v3/orders/<id>``."""
    order = {
        "id": order_id,
        "number": str(order_id),
        "status": "processing",
        "date_modified_gmt": "2026-10-01T12:00:00",
        "billing": {
            "first_name": "Ana",
            "last_name": "López",
            "email": "ana@example.com",
            "phone": "11 5555-0000",
            "address_1": "Rivadavia 100",
            "city": "CABA",
            "state": "C",
            "postcode": "1000",
            "country": "AR",
        },
        "shipping": {
            "first_name": "María",
            "last_name": "Gómez",
            "company": "",
            "address_1": "Av. Corrientes 1234",
            "address_2": "Piso 3 B",
            "city": "Córdoba",
            "state": "X",
            "postcode": "5000",
            "country": "AR",
            "phone": "351 444-0000",
        },
        "line_items": [{"name": "Remera", "sku": "REM-1", "quantity": 2}],
        "shipping_lines": [{"method_title": "Envío a domicilio"}],
    }
    order.update(overrides)
    return order


class FakeWoo:
    """Simula la API REST de una tienda WooCommerce (todo lo que pasa por
    ``requests.request``) y la raíz pública ``/wp-json/`` (``requests.get``)."""

    def __init__(self):
        self.calls = []
        self.orders = {}
        self.notes = {}
        self.webhooks = []
        self.accepted_mode = "basic"  # "query" = hosting que se come el header
        self.valid_keys = ("ck_bueno", "cs_bueno")
        self.store_name = "Mi Tienda Woo"
        # Ajustes de nuestro plugin de impresión; None = no está instalado.
        self.plugin_settings = None

    def _authorized(self, auth, params):
        if auth is not None:
            return self.accepted_mode == "basic" and tuple(auth) == self.valid_keys
        return (params or {}).get("consumer_key") == self.valid_keys[0] and (params or {}).get(
            "consumer_secret"
        ) == self.valid_keys[1]

    def request(self, method, url, params=None, json=None, auth=None, headers=None, timeout=None):
        path = urlparse(url).path.split("/wp-json/wc/v3/", 1)[-1]
        self.calls.append({"method": method, "path": path, "params": dict(params or {}), "json": json, "auth": auth})
        if not self._authorized(auth, params):
            return fake_response(401, {"code": "woocommerce_rest_cannot_view"})

        if method == "GET" and path == "orders":
            ordered = sorted(self.orders.values(), key=lambda order: order["id"])
            per_page, page = int(params.get("per_page", 10)), int(params.get("page", 1))
            chunk = ordered[(page - 1) * per_page : page * per_page]
            response = fake_response(200, chunk)
            response.headers = {"X-WP-TotalPages": str(max(1, -(-len(ordered) // per_page)))}
            return response
        if method == "GET" and path.startswith("orders/") and path.endswith("/notes"):
            order_id = int(path.split("/")[1])
            return fake_response(200, self.notes.get(order_id, []))
        if method == "POST" and path.startswith("orders/") and path.endswith("/notes"):
            order_id = int(path.split("/")[1])
            self.notes.setdefault(order_id, []).append(json)
            return fake_response(201, json)
        if path.startswith("orders/"):
            order_id = int(path.split("/")[1])
            if order_id not in self.orders:
                return fake_response(404, {"code": "woocommerce_rest_shop_order_invalid_id"})
            if method == "PUT":
                self.orders[order_id].update(json)
            return fake_response(200, self.orders[order_id])
        if method == "GET" and path == "webhooks":
            return fake_response(200, self.webhooks)
        if method == "POST" and path == "webhooks":
            hook = dict(json, id=len(self.webhooks) + 1)
            self.webhooks.append(hook)
            return fake_response(201, hook)
        if method == "PUT" and path.startswith("webhooks/"):
            hook_id = int(path.split("/")[1])
            for hook in self.webhooks:
                if hook["id"] == hook_id:
                    hook.update(json)
            return fake_response(200, {})
        if method == "POST" and path == "settings/rotulos/batch":
            if self.plugin_settings is None:
                return fake_response(404, {"code": "rest_setting_setting_group_invalid"})
            for item in json["update"]:
                self.plugin_settings[item["id"]] = item["value"]
            return fake_response(200, {"update": json["update"]})
        raise AssertionError(f"Pedido no simulado: {method} {path}")

    def get(self, url, headers=None, timeout=None):
        return fake_response(200, {"name": self.store_name, "url": SITE})

    def calls_to(self, method, path):
        return [call for call in self.calls if call["method"] == method and call["path"] == path]


class WooTestMixin:
    def setUp(self):
        super().setUp()
        self.woo = FakeWoo()
        for target, side_effect in (("request", self.woo.request), ("get", self.woo.get)):
            patcher = patch(f"apps.integrations.providers.woocommerce.provider.requests.{target}", side_effect=side_effect)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _connection(self, **overrides):
        values = {
            "platform": "woocommerce",
            "external_store_id": EXTERNAL_ID,
            "store_url": SITE,
            "access_token": credentials_token(*self.woo.valid_keys),
        }
        values.update(overrides)
        return StoreConnection.objects.create(**values)

    def _redirect_params(self, response):
        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertTrue(location.startswith(f"{FRONTEND}/tiendas.html"), location)
        return {key: values[0] for key, values in parse_qs(urlparse(location).query).items()}


class SiteUrlTests(TestCase):
    def setUp(self):
        self.provider = get_provider("woocommerce")

    def test_acepta_lo_que_pegaria_un_comerciante(self):
        cases = {
            "mitienda.com.ar": SITE,
            "https://MiTienda.com.ar/": SITE,
            "https://mitienda.com.ar/wp-admin/admin.php?page=wc-settings": SITE,
            "https://mitienda.com.ar/tienda/": f"{SITE}/tienda",
        }
        for value, expected in cases.items():
            self.assertEqual(self.provider.normalize_shop_domain(value), expected, value)

    def test_rechaza_http_y_cualquier_cosa(self):
        for value in ("http://mitienda.com.ar", "", "mitienda", "https://", "con espacios.com"):
            with self.assertRaises(ValueError, msg=value):
                self.provider.normalize_shop_domain(value)


class NormalizeOrderTests(TestCase):
    def setUp(self):
        self.provider = get_provider("woocommerce")

    def test_traduce_lo_que_necesita_el_rotulo(self):
        normalized = self.provider.normalize_order(woo_order(55))

        self.assertEqual((normalized.external_id, normalized.external_number), ("55", "55"))
        self.assertEqual(normalized.recipient_name, "María Gómez")
        self.assertEqual((normalized.street, normalized.number), ("Av. Corrientes", "1234"))
        self.assertEqual(normalized.reference, "Piso 3 B")
        self.assertEqual((normalized.city, normalized.state, normalized.postal_code), ("Córdoba", "Córdoba", "5000"))
        self.assertEqual(normalized.country, "Argentina")
        self.assertEqual(normalized.shipping_option, "Envío a domicilio")
        self.assertEqual(normalized.description, "2x Remera")
        self.assertEqual(normalized.status, "")
        self.assertEqual(normalized.external_updated_at.isoformat(), "2026-10-01T12:00:00+00:00")
        # Ni email ni teléfono: ni en el pedido ni en la copia guardada.
        self.assertEqual((normalized.contact_email, normalized.contact_phone), ("", ""))
        self.assertNotIn("email", normalized.raw["billing"])
        self.assertNotIn("phone", normalized.raw["billing"])
        self.assertNotIn("phone", normalized.raw["shipping"])

    def test_sin_envio_usa_la_direccion_de_facturacion(self):
        normalized = self.provider.normalize_order(woo_order(56, shipping={}))

        self.assertEqual(normalized.recipient_name, "Ana López")
        self.assertEqual((normalized.street, normalized.number, normalized.state), ("Rivadavia", "100", "Ciudad Autónoma de Buenos Aires"))

    def test_estados(self):
        for woo_status, local in (("cancelled", "cancelled"), ("refunded", "cancelled"), ("completed", "dispatched"), ("on-hold", "")):
            self.assertEqual(self.provider.normalize_order(woo_order(1, status=woo_status)).status, local, woo_status)

    def test_provincia_fuera_de_argentina_queda_como_viene(self):
        shipping = dict(woo_order(1)["shipping"], country="UY", state="MO")
        normalized = self.provider.normalize_order(woo_order(1, shipping=shipping))
        self.assertEqual((normalized.state, normalized.country), ("MO", "UY"))


@override_settings(**WOO_SETTINGS)
class AutomaticConnectTests(WooTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")

    def test_la_url_lleva_a_autorizar_en_el_sitio_de_la_tienda(self):
        response = self.client.get(
            "/api/v1/integrations/woocommerce/install-url/", {"shop": "mitienda.com.ar"}, **auth_headers_for(self.user)
        )

        self.assertEqual(response.status_code, 200, response.data)
        url = urlparse(response.data["authorize_url"])
        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", f"{SITE}/wc-auth/v1/authorize")
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        self.assertEqual(query["scope"], "read_write")
        self.assertEqual(query["app_name"], "Rotulos perso")
        self.assertEqual(query["callback_url"], f"{BASE_URL}/api/v1/integrations/woocommerce/keys/")
        self.assertEqual(query["return_url"], f"{BASE_URL}/api/v1/integrations/woocommerce/return/")
        state = signing.loads(query["user_id"], salt=STATE_SALT)
        self.assertEqual((state["u"], state["p"], state["s"]), (self.user.pk, "woocommerce", SITE))

    def test_las_claves_que_postea_woocommerce_conectan_la_tienda(self):
        state = make_oauth_state(self.user, "woocommerce", SITE)

        response = self.client.post(
            "/api/v1/integrations/woocommerce/keys/",
            {"key_id": 1, "user_id": state, "consumer_key": "ck_bueno", "consumer_secret": "cs_bueno", "key_permissions": "read_write"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        connection = StoreConnection.objects.get(platform="woocommerce", external_store_id=EXTERNAL_ID)
        self.assertEqual(connection.owner, self.user)
        self.assertEqual(connection.store_url, SITE)
        self.assertEqual(json.loads(connection.access_token), {"key": "ck_bueno", "secret": "cs_bueno"})
        # No le habla a la tienda mientras ella espera la respuesta.
        self.assertEqual(self.woo.calls, [])

        returned = self.client.get("/api/v1/integrations/woocommerce/return/", {"success": "1", "user_id": state})
        self.assertEqual(self._redirect_params(returned), {"store_connected": str(connection.pk)})

    def test_state_alterado_no_conecta(self):
        response = self.client.post(
            "/api/v1/integrations/woocommerce/keys/",
            {"user_id": "inventado", "consumer_key": "ck_bueno", "consumer_secret": "cs_bueno"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(StoreConnection.objects.exists())

    def test_rechazar_en_woocommerce_vuelve_con_el_aviso(self):
        returned = self.client.get(
            "/api/v1/integrations/woocommerce/return/",
            {"success": "0", "user_id": make_oauth_state(self.user, "woocommerce", SITE)},
        )
        self.assertEqual(self._redirect_params(returned), {"store_error": "authorization_cancelled"})


@override_settings(**WOO_SETTINGS)
class ManualConnectTests(WooTestMixin, APITestCase):
    URL = "/api/v1/integrations/woocommerce/connect-manual/"

    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")

    def _connect(self, **data):
        payload = {"site_url": "mitienda.com.ar", "consumer_key": "ck_bueno", "consumer_secret": "cs_bueno"}
        payload.update(data)
        return self.client.post(self.URL, payload, format="json", **auth_headers_for(self.user))

    def test_claves_validas_conectan_y_lanzan_la_configuracion(self):
        response = self._connect()

        self.assertEqual(response.status_code, 201, response.data)
        connection = StoreConnection.objects.get(pk=response.data["id"])
        self.assertEqual((connection.owner, connection.store_url), (self.user, SITE))
        self.assertTrue(IntegrationEvent.objects.filter(connection=connection, event_type="internal/store_setup").exists())

    def test_claves_rechazadas_por_la_tienda(self):
        response = self._connect(consumer_secret="cs_malo")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(StoreConnection.objects.exists())

    def test_formato_de_claves_invalido(self):
        self.assertEqual(self._connect(consumer_key="cualquiera").status_code, 400)
        self.assertEqual(self.woo.calls, [])

    def test_sitio_sin_https(self):
        self.assertEqual(self._connect(site_url="http://mitienda.com.ar").status_code, 400)

    def test_hosting_que_se_come_el_header_usa_la_query_string(self):
        self.woo.accepted_mode = "query"

        response = self._connect()

        self.assertEqual(response.status_code, 201, response.data)
        connection = StoreConnection.objects.get(pk=response.data["id"])
        self.assertEqual(connection.preferences["woo_auth"], "query")

        # Y los pedidos siguientes van directo por query string.
        self.woo.calls.clear()
        get_provider("woocommerce").api_request(connection, "GET", "orders", params={"per_page": 1, "page": 1})
        self.assertIsNone(self.woo.calls[0]["auth"])
        self.assertEqual(len(self.woo.calls), 1)


@override_settings(**WOO_SETTINGS)
class SetupAndWebhookTests(WooTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("comercio@example.com")
        self.connection = self._connection(owner=self.owner)

    def _delivery_url(self):
        return f"{BASE_URL}{WEBHOOKS_URL}?store={self.connection.pk}"

    def _post_webhook(self, topic, payload, *, secret=None, store=None):
        body = json.dumps(payload).encode("utf-8")
        self.connection.refresh_from_db()
        key = (secret or self.connection.webhook_secret).encode()
        signature = base64.b64encode(hmac.new(key, body, hashlib.sha256).digest()).decode()
        return self.client.generic(
            "POST",
            f"{WEBHOOKS_URL}?store={store or self.connection.pk}",
            body,
            content_type="application/json",
            HTTP_X_WC_WEBHOOK_SIGNATURE=signature,
            HTTP_X_WC_WEBHOOK_TOPIC=topic,
            HTTP_X_WC_WEBHOOK_SOURCE=f"{SITE}/",
        )

    def test_configurar_lee_el_nombre_crea_los_webhooks_e_importa(self):
        self.woo.orders = {1: woo_order(1), 2: woo_order(2), 3: woo_order(3)}

        enqueue_store_setup(self.connection)
        process_due_events()
        process_due_events()
        process_due_events()

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.name, "Mi Tienda Woo")
        created = [call["json"] for call in self.woo.calls_to("POST", "webhooks")]
        self.assertEqual([hook["topic"] for hook in created], ["order.created", "order.updated"])
        self.assertTrue(all(hook["delivery_url"] == self._delivery_url() for hook in created))
        self.assertTrue(all(hook["secret"] == self.connection.webhook_secret for hook in created))
        self.assertEqual(sorted(Order.objects.values_list("external_id", flat=True)), ["1", "2", "3"])
        self.assertFalse(IntegrationEvent.objects.exclude(status=IntegrationEvent.Status.DONE).exists())

    def test_webhook_desactivado_por_fallas_se_reactiva(self):
        self.woo.webhooks = [
            {"id": 7, "topic": "order.created", "delivery_url": self._delivery_url(), "status": "disabled"},
            {"id": 8, "topic": "order.updated", "delivery_url": self._delivery_url(), "status": "active"},
        ]

        get_provider("woocommerce").register_webhooks(self.connection, f"{BASE_URL}{WEBHOOKS_URL}", ("order.created", "order.updated"))

        self.assertEqual(self.woo.calls_to("POST", "webhooks"), [])
        self.assertEqual(self.woo.webhooks[0]["status"], "active")
        self.assertEqual(self.woo.webhooks[0]["secret"], self.connection.webhook_secret)

    def test_configuracion_y_repaso_en_la_misma_tanda_comparten_el_secreto(self):
        # El worker toma los eventos de a tandas con su conexión ya cargada:
        # el repaso trae una copia vieja, sin secreto, y no debe inventar
        # otro distinto del que ya se mandó a WooCommerce.
        enqueue_store_setup(self.connection)
        enqueue_due_reconciliations()
        process_due_events()

        self.connection.refresh_from_db()
        created = [call["json"] for call in self.woo.calls_to("POST", "webhooks")]
        self.assertTrue(created)
        self.assertTrue(all(hook["secret"] == self.connection.webhook_secret for hook in created))

    def test_aviso_firmado_con_el_secreto_de_la_tienda_trae_el_pedido(self):
        get_provider("woocommerce").ensure_webhook_secret(self.connection)
        self.woo.orders = {77: woo_order(77)}

        response = self._post_webhook("order.created", woo_order(77))
        process_due_events()

        self.assertEqual(response.status_code, 200)
        order = Order.objects.get(store_connection=self.connection, external_id="77")
        self.assertEqual(order.user, self.owner)
        event = IntegrationEvent.objects.get(event_type="order.created")
        self.assertNotIn("billing", event.payload)

    def test_firma_con_otro_secreto_se_rechaza(self):
        get_provider("woocommerce").ensure_webhook_secret(self.connection)
        response = self._post_webhook("order.created", woo_order(77), secret="otro-secreto")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(IntegrationEvent.objects.exists())

    def test_tienda_sin_secreto_todavia_no_acepta_avisos(self):
        response = self._post_webhook("order.created", woo_order(77), secret="cualquiera")
        self.assertEqual(response.status_code, 401)

    def test_el_ping_de_creacion_se_contesta_sin_encolar(self):
        response = self.client.post(
            f"{WEBHOOKS_URL}?store={self.connection.pk}", "webhook_id=12", content_type="application/x-www-form-urlencoded"
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(IntegrationEvent.objects.exists())


@override_settings(**WOO_SETTINGS)
class PushFulfillmentTests(WooTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("comercio@example.com")
        self.connection = self._connection(owner=self.owner)
        self.woo.orders = {90: woo_order(90)}
        from apps.orders.ingestion import upsert_store_order

        order, _ = upsert_store_order(self.connection, get_provider("woocommerce").normalize_order(woo_order(90)))
        self.order = Order.objects.get(pk=order.pk)

    def _ship(self, **data):
        data.setdefault("status", "dispatched")
        response = self.client.post(f"/api/v1/orders/{self.order.pk}/ship/", data, format="json", **auth_headers_for(self.owner))
        self.assertEqual(response.status_code, 200, response.data)
        process_due_events()

    def test_despachar_completa_el_pedido_y_deja_la_nota_con_el_seguimiento(self):
        self._ship(carrier="Correo Argentino", tracking_number="AR123", tracking_url="https://seguimiento.example.com/AR123")

        self.assertEqual(self.woo.orders[90]["status"], "completed")
        note = self.woo.notes[90][0]
        self.assertTrue(note["customer_note"])
        self.assertIn("Correo Argentino", note["note"])
        self.assertIn("AR123", note["note"])
        self.assertIn("https://seguimiento.example.com/AR123", note["note"])

    def test_el_mismo_seguimiento_no_repite_la_nota(self):
        self.woo.notes[90] = [{"note": "Número de seguimiento: AR123.", "customer_note": True}]
        self._ship(tracking_number="AR123")
        self.assertEqual(len(self.woo.notes[90]), 1)

    def test_pedido_cancelado_en_la_tienda_no_se_completa(self):
        self.woo.orders[90]["status"] = "cancelled"
        self._ship()
        self.assertEqual(self.woo.orders[90]["status"], "cancelled")


@override_settings(**WOO_SETTINGS, INTEGRATIONS_RECONCILE_MINUTES=30)
class ReconciliationTests(WooTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.connection = self._connection(owner=make_user("comercio@example.com"))

    def test_se_encola_una_vez_por_intervalo(self):
        now = timezone.now()

        self.assertEqual(enqueue_due_reconciliations(now), 1)
        self.assertEqual(enqueue_due_reconciliations(now + timedelta(minutes=5)), 0)
        self.assertEqual(enqueue_due_reconciliations(now + timedelta(minutes=31)), 1)

    def test_tiendas_de_otras_plataformas_o_sin_duenio_no_se_repasan(self):
        StoreConnection.objects.create(platform="tiendanube", external_store_id="1", access_token="t", owner=make_user("tn@example.com"))
        self._connection(external_store_id="otra.com", owner=None)
        self.assertEqual(enqueue_due_reconciliations(), 1)

    def test_el_repaso_reactiva_webhooks_y_trae_los_modificados(self):
        self.woo.orders = {5: woo_order(5, status="cancelled")}
        enqueue_due_reconciliations()

        process_due_events()

        listed = self.woo.calls_to("GET", "orders")[0]["params"]
        self.assertIn("modified_after", listed)
        self.assertEqual(listed["dates_are_gmt"], "true")
        self.assertEqual(len(self.woo.calls_to("POST", "webhooks")), 2)
        self.assertEqual(Order.objects.get(external_id="5").status, Order.Status.CANCELLED)
        self.assertTrue(IntegrationEvent.objects.filter(event_type=RECONCILE_ORDERS_EVENT, status="done").exists())
        self.assertFalse(IntegrationEvent.objects.filter(event_type=IMPORT_ORDERS_EVENT).exists())
