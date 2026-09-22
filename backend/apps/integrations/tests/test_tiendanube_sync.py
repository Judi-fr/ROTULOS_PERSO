"""Tests de la fase 3 de Tiendanube: receptor de webhooks, sincronización de
pedidos por el worker, desinstalación, registro de webhooks e importación
inicial. La API de Tiendanube se simula con ``FakeTiendanubeApi``."""

import hashlib
import hmac
import json
import re
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.orders.models import Order

from ..events import enqueue_event, process_due_events
from ..handlers import WEBHOOK_EVENTS
from ..models import IntegrationEvent, StoreConnection
from ..providers.base import OAuthResult, StoreInfo
from ..stores import (
    IMPORT_ORDERS_EVENT,
    STORE_SETUP_EVENT,
    claim_store,
    connect_store,
    enqueue_initial_import,
    enqueue_store_setup,
    make_claim_token,
)
from .test_store_integration_base import tiendanube_order
from .test_tiendanube_oauth import TEST_SETTINGS, fake_response, make_user

WEBHOOKS_URL = "/api/v1/integrations/tiendanube/webhooks/"
PUBLIC_BASE_URL = "https://rotulos.example.com"
EXPECTED_WEBHOOK_URL = f"{PUBLIC_BASE_URL}{WEBHOOKS_URL}"
API_PATH = re.compile(r"^https://api\.tiendanube\.com/[^/]+/[^/]+/(?P<path>.*)$")


class FakeTiendanubeApi:
    """Reemplaza ``requests.request`` del proveedor. Cada ruta es
    ``(status, data)`` o una función ``params -> (status, data)``; una ruta
    no cargada responde 404."""

    def __init__(self):
        self.routes = {}
        self.calls = []

    def set(self, method, path, status=200, data=None):
        self.routes[(method, path)] = (status, data)

    def set_handler(self, method, path, handler):
        self.routes[(method, path)] = handler

    def calls_to(self, method, path):
        return [call for call in self.calls if call["method"] == method and call["path"] == path]

    def __call__(self, method, url, headers=None, timeout=None, params=None, json=None):
        path = API_PATH.match(url).group("path")
        self.calls.append({"method": method, "path": path, "params": params, "json": json})
        route = self.routes.get((method, path), (404, {}))
        status, data = route(params) if callable(route) else route
        return fake_response(status, data)


class TiendanubeTestCase(APITestCase):
    def setUp(self):
        self.owner = make_user("comercio@example.com")
        self.connection = StoreConnection.objects.create(
            owner=self.owner,
            platform="tiendanube",
            external_store_id="555",
            name="Tienda de prueba",
            access_token="tok-1",
        )
        self.api = FakeTiendanubeApi()
        patcher = patch("apps.integrations.providers.tiendanube.requests.request", side_effect=self.api)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _event(self, event_type, resource_id="871254203", connection=None, payload=None):
        event, _ = enqueue_event(
            platform="tiendanube",
            event_type=event_type,
            connection=connection or self.connection,
            resource_id=resource_id,
            payload=payload or {"store_id": 555, "event": event_type, "id": resource_id},
        )
        return event


@override_settings(**TEST_SETTINGS)
class TiendanubeWebhookReceiverTests(TiendanubeTestCase):
    def _post(self, payload=None, raw=None, signature=None):
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        if signature is None:
            signature = hmac.new(b"secreto-de-prueba", body, hashlib.sha256).hexdigest()
        return self.client.generic(
            "POST", WEBHOOKS_URL, body, content_type="application/json", HTTP_X_LINKEDSTORE_HMAC_SHA256=signature
        )

    def test_aviso_firmado_se_encola_y_responde_enseguida(self):
        response = self._post({"store_id": 555, "event": "order/paid", "id": 871254203})

        self.assertEqual(response.status_code, 200, response.content)
        event = IntegrationEvent.objects.get()
        self.assertEqual(event.connection, self.connection)
        self.assertEqual(event.event_type, "order/paid")
        self.assertEqual(event.resource_id, "871254203")
        self.assertEqual(event.status, IntegrationEvent.Status.PENDING)
        # Responder no llama a la API: eso lo hace el worker.
        self.assertEqual(self.api.calls, [])

    def test_firma_invalida_no_encola_nada(self):
        response = self._post({"store_id": 555, "event": "order/paid", "id": 1}, signature="firma-falsa")

        self.assertEqual(response.status_code, 401)
        self.assertFalse(IntegrationEvent.objects.exists())

    def test_aviso_de_una_tienda_desconocida_se_acepta_para_depurar(self):
        response = self._post({"store_id": 999, "event": "order/paid", "id": 1})

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(IntegrationEvent.objects.get().connection)

    def test_json_invalido_o_incompleto(self):
        self.assertEqual(self._post(raw=b"no-es-json").status_code, 400)
        self.assertEqual(self._post({"event": "order/paid"}).status_code, 400)
        self.assertFalse(IntegrationEvent.objects.exists())

    def test_no_se_pueden_inyectar_eventos_internos(self):
        response = self._post({"store_id": 555, "event": IMPORT_ORDERS_EVENT, "id": 1})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(IntegrationEvent.objects.exists())


@override_settings(**TEST_SETTINGS, INTEGRATIONS_EVENT_RETRY_BASE_SECONDS=60)
class TiendanubeOrderSyncTests(TiendanubeTestCase):
    def test_aviso_de_pedido_trae_el_pedido_completo_y_lo_crea(self):
        self.api.set("GET", "orders/871254203", 200, tiendanube_order())
        event = self._event("order/paid")

        process_due_events()

        event.refresh_from_db()
        self.assertEqual(event.status, IntegrationEvent.Status.DONE, event.last_error)
        order = Order.objects.get()
        self.assertEqual(order.store_connection, self.connection)
        self.assertEqual(order.user, self.owner)
        self.assertEqual(order.external_number, "1001")
        self.connection.refresh_from_db()
        self.assertIsNotNone(self.connection.last_synced_at)

    def test_entran_todos_los_pedidos_aunque_no_esten_pagados(self):
        self.api.set("GET", "orders/871254203", 200, tiendanube_order(payment_status="pending"))
        self._event("order/created")

        process_due_events()

        self.assertEqual(Order.objects.count(), 1)

    def test_creado_y_despues_actualizado_es_un_solo_pedido(self):
        self.api.set("GET", "orders/871254203", 200, tiendanube_order())
        self._event("order/created")
        process_due_events()

        updated = tiendanube_order(updated_at="2026-09-11T09:00:00+0000")
        updated["shipping_address"] = {**updated["shipping_address"], "city": "Rosario"}
        self.api.set("GET", "orders/871254203", 200, updated)
        self._event("order/updated")
        process_due_events()

        order = Order.objects.get()
        self.assertEqual(order.address.city, "Rosario")

    def test_token_rechazado_falla_sin_reintentar_y_marca_la_tienda(self):
        self.api.set("GET", "orders/871254203", 401, {})
        event = self._event("order/paid")

        process_due_events()

        event.refresh_from_db()
        self.connection.refresh_from_db()
        self.assertEqual(event.status, IntegrationEvent.Status.FAILED)
        self.assertEqual(self.connection.status, StoreConnection.Status.ERROR)
        self.assertIn("401", self.connection.last_error)

    def test_pedido_inexistente_falla_sin_reintentar(self):
        event = self._event("order/paid", resource_id="404404")

        process_due_events()

        event.refresh_from_db()
        self.assertEqual(event.status, IntegrationEvent.Status.FAILED)
        self.assertEqual(event.attempts, 1)

    def test_tiendanube_caida_se_reintenta(self):
        self.api.set("GET", "orders/871254203", 503, {})
        event = self._event("order/paid")

        process_due_events()

        event.refresh_from_db()
        self.assertEqual(event.status, IntegrationEvent.Status.PENDING)
        self.assertFalse(Order.objects.exists())

    def test_tienda_sin_vincular_o_desconectada_no_crea_pedidos(self):
        self.api.set("GET", "orders/871254203", 200, tiendanube_order())
        unclaimed = StoreConnection.objects.create(platform="tiendanube", external_store_id="777", access_token="t")
        revoked = StoreConnection.objects.create(
            owner=self.owner, platform="tiendanube", external_store_id="888", status=StoreConnection.Status.REVOKED
        )
        events = [self._event("order/paid", connection=unclaimed), self._event("order/paid", connection=revoked)]

        process_due_events()

        for event in events:
            event.refresh_from_db()
            self.assertEqual(event.status, IntegrationEvent.Status.FAILED)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(self.api.calls, [])

    def test_desinstalar_la_app_desconecta_la_tienda(self):
        event = self._event("app/uninstalled", resource_id="")

        process_due_events()

        event.refresh_from_db()
        self.connection.refresh_from_db()
        self.assertEqual(event.status, IntegrationEvent.Status.DONE)
        self.assertEqual(self.connection.status, StoreConnection.Status.REVOKED)
        self.assertEqual(self.connection.access_token, "")

    def test_el_worker_tiene_los_handlers_registrados(self):
        self.api.set("GET", "orders/871254203", 200, tiendanube_order())
        self._event("order/paid")

        call_command("run_integrations_worker", "--once", stdout=StringIO())

        self.assertEqual(Order.objects.count(), 1)


@override_settings(**TEST_SETTINGS, INTEGRATIONS_PUBLIC_BASE_URL=PUBLIC_BASE_URL)
class TiendanubeStoreSetupTests(TiendanubeTestCase):
    def test_registra_solo_los_webhooks_que_faltan_y_lanza_la_importacion(self):
        self.api.set("GET", "webhooks", 200, [{"id": 1, "event": "order/created", "url": EXPECTED_WEBHOOK_URL}])
        self.api.set("POST", "webhooks", 201, {})
        setup, _ = enqueue_store_setup(self.connection)

        process_due_events()

        setup.refresh_from_db()
        self.assertEqual(setup.status, IntegrationEvent.Status.DONE, setup.last_error)
        posted = [call["json"] for call in self.api.calls_to("POST", "webhooks")]
        self.assertEqual([item["event"] for item in posted], [e for e in WEBHOOK_EVENTS if e != "order/created"])
        self.assertTrue(all(item["url"] == EXPECTED_WEBHOOK_URL for item in posted))
        self.assertTrue(IntegrationEvent.objects.filter(event_type=IMPORT_ORDERS_EVENT).exists())

    def test_reconfigurar_no_duplica_webhooks(self):
        already = [{"id": i, "event": event, "url": EXPECTED_WEBHOOK_URL} for i, event in enumerate(WEBHOOK_EVENTS)]
        self.api.set("GET", "webhooks", 200, already)
        enqueue_store_setup(self.connection)

        process_due_events()

        self.assertEqual(self.api.calls_to("POST", "webhooks"), [])

    @override_settings(INTEGRATIONS_PUBLIC_BASE_URL="")
    def test_sin_url_publica_avisa_pero_igual_importa(self):
        enqueue_store_setup(self.connection)

        process_due_events()

        self.connection.refresh_from_db()
        self.assertIn("INTEGRATIONS_PUBLIC_BASE_URL", self.connection.last_error)
        self.assertEqual(self.api.calls_to("GET", "webhooks"), [])
        self.assertTrue(IntegrationEvent.objects.filter(event_type=IMPORT_ORDERS_EVENT).exists())

    def test_tienda_sin_dueno_registra_webhooks_pero_no_importa(self):
        self.api.set("GET", "webhooks", 200, [])
        self.api.set("POST", "webhooks", 201, {})
        unclaimed = StoreConnection.objects.create(platform="tiendanube", external_store_id="777", access_token="t")
        enqueue_store_setup(unclaimed)

        process_due_events()

        self.assertEqual(len(self.api.calls_to("POST", "webhooks")), len(WEBHOOK_EVENTS))
        self.assertFalse(IntegrationEvent.objects.filter(event_type=IMPORT_ORDERS_EVENT).exists())

    def test_conectar_una_tienda_encola_su_configuracion(self):
        class FakeProvider:
            platform = "tiendanube"

            def exchange_code(self, code):
                return OAuthResult(access_token="tok-nuevo", external_store_id="999")

            def get_store_info(self, connection):
                return StoreInfo(name="Otra tienda")

        result = connect_store(FakeProvider(), "codigo", user=self.owner)

        self.assertTrue(
            IntegrationEvent.objects.filter(connection=result.connection, event_type=STORE_SETUP_EVENT).exists()
        )

    def test_vincular_una_tienda_lanza_la_importacion(self):
        unclaimed = StoreConnection.objects.create(platform="tiendanube", external_store_id="777", access_token="t")

        claim_store(make_claim_token(unclaimed), self.owner)

        self.assertTrue(IntegrationEvent.objects.filter(connection=unclaimed, event_type=IMPORT_ORDERS_EVENT).exists())


@override_settings(**TEST_SETTINGS, TIENDANUBE_ORDERS_PAGE_SIZE=2, INTEGRATIONS_INITIAL_IMPORT_DAYS=30)
class TiendanubeInitialImportTests(TiendanubeTestCase):
    def test_importa_pagina_por_pagina_hasta_la_ultima(self):
        pages = {1: [tiendanube_order(id=1), tiendanube_order(id=2)], 2: [tiendanube_order(id=3)]}
        self.api.set_handler("GET", "orders", lambda params: (200, pages[params["page"]]))
        enqueue_initial_import(self.connection)

        process_due_events()  # página 1 -> encola la 2
        process_due_events()  # página 2 -> última (menos de 2 pedidos)

        self.assertEqual(sorted(Order.objects.values_list("external_id", flat=True)), ["1", "2", "3"])
        calls = self.api.calls_to("GET", "orders")
        self.assertEqual([call["params"]["page"] for call in calls], [1, 2])
        self.assertEqual(calls[0]["params"]["per_page"], 2)
        self.assertIn("created_at_min", calls[0]["params"])
        self.assertFalse(
            IntegrationEvent.objects.exclude(status=IntegrationEvent.Status.DONE).exists()
        )

    def test_pasada_la_ultima_pagina_tiendanube_responde_404_y_termina(self):
        def orders(params):
            if params["page"] == 1:
                return 200, [tiendanube_order(id=1), tiendanube_order(id=2)]
            return 404, {"description": "Last page is 1"}

        self.api.set_handler("GET", "orders", orders)
        enqueue_initial_import(self.connection)

        process_due_events()
        process_due_events()
        process_due_events()

        self.assertEqual(Order.objects.count(), 2)
        self.assertEqual(len(self.api.calls_to("GET", "orders")), 2)
        self.assertFalse(IntegrationEvent.objects.exclude(status=IntegrationEvent.Status.DONE).exists())

    def test_reimportar_no_duplica_pedidos(self):
        self.api.set("GET", "orders", 200, [tiendanube_order(id=1)])
        enqueue_initial_import(self.connection)
        process_due_events()
        enqueue_initial_import(self.connection)
        process_due_events()

        self.assertEqual(Order.objects.count(), 1)
