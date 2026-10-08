"""Tests de VTEX: nombre de la cuenta, conexión con appKey/appToken, hook de
pedidos (y su ping), feed como respaldo, pedidos, y el despacho con el
seguimiento en la factura. La VTEX simulada está en ``fake_vtex.py``."""

import json
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.integrations.events import process_due_events
from apps.integrations.models import IntegrationEvent, StoreConnection
from apps.integrations.providers import get_provider
from apps.integrations.providers.vtex.provider import FOREIGN_HOOK_ERROR
from apps.integrations.stores import (
    PUSH_FULFILLMENT_EVENT,
    RECONCILE_ORDERS_EVENT,
    enqueue_due_reconciliations,
    enqueue_store_setup,
)
from apps.integrations.tests.helpers import auth_headers_for, make_user
from apps.orders.ingestion import upsert_store_order
from apps.orders.models import Order

from .fake_vtex import ACCOUNT, BASE_URL, KEYS, VTEX_SETTINGS, WEBHOOKS_URL, VtexTestMixin, invoice, vtex_order


class AccountNameTests(TestCase):
    def setUp(self):
        self.provider = get_provider("vtex")

    def test_acepta_lo_que_pegaria_un_comerciante(self):
        for value in (
            "micuenta",
            "MiCuenta",
            "https://micuenta.myvtex.com/admin",
            "micuenta.vtexcommercestable.com.br",
            "https://prueba--micuenta.myvtex.com/",
        ):
            self.assertEqual(self.provider.normalize_shop_domain(value), ACCOUNT, value)

    def test_rechaza_un_dominio_propio_o_cualquier_cosa(self):
        for value in ("", "www.mitienda.com.ar", "con espacios", "https://"):
            with self.assertRaises(ValueError, msg=value):
                self.provider.normalize_shop_domain(value)


class NormalizeOrderTests(TestCase):
    def setUp(self):
        self.provider = get_provider("vtex")

    def test_traduce_lo_que_necesita_el_rotulo(self):
        normalized = self.provider.normalize_order(vtex_order("1172452900788-01"))

        self.assertEqual((normalized.external_id, normalized.external_number), ("1172452900788-01", "1172452900788-01"))
        self.assertEqual(normalized.recipient_name, "María Gómez")
        self.assertEqual((normalized.street, normalized.number), ("Av. Colón", "1234"))
        self.assertEqual(normalized.reference, "Piso 3 B, Timbre roto, Centro")
        self.assertEqual((normalized.city, normalized.state, normalized.postal_code), ("Córdoba", "Córdoba", "5000"))
        self.assertEqual(normalized.country, "Argentina")
        self.assertEqual(normalized.shipping_option, "Envío a domicilio")
        self.assertEqual(normalized.description, "2x Remera")
        self.assertEqual(normalized.status, "")
        self.assertEqual(normalized.external_updated_at.isoformat(), "2026-10-01T12:00:00.701074+00:00")
        # Nada del comprador fuera de lo que va en el rótulo, ni pagos.
        self.assertEqual((normalized.contact_email, normalized.contact_phone), ("", ""))
        stored = json.dumps(normalized.raw)
        for secret in ("ana@example.com", "20123456789", "4111", "351444"):
            self.assertNotIn(secret, stored)

    def test_sin_numero_lo_separa_de_la_calle_y_codigo_de_provincia(self):
        address = dict(vtex_order("1")["shippingData"]["address"], street="Rivadavia 100", number="", state="C", neighborhood="")
        normalized = self.provider.normalize_order(vtex_order("1", shippingData={"address": address}))
        self.assertEqual((normalized.street, normalized.number), ("Rivadavia", "100"))
        self.assertEqual(normalized.state, "Ciudad Autónoma de Buenos Aires")

    def test_estados(self):
        cases = (
            ({"status": "canceled"}, "cancelled"),
            ({"status": "invoiced", "packageAttachment": {"packages": [invoice()]}}, ""),
            ({"status": "invoiced", "packageAttachment": {"packages": [invoice(trackingNumber="AR1")]}}, "dispatched"),
            (
                {"status": "invoiced", "packageAttachment": {"packages": [invoice(trackingNumber="AR1", courierStatus={"finished": True})]}},
                "delivered",
            ),
            ({"status": "handling"}, ""),
        )
        for overrides, local in cases:
            self.assertEqual(self.provider.normalize_order(vtex_order("1", **overrides)).status, local, overrides)

    def test_pedido_sin_id(self):
        with self.assertRaises(ValueError):
            self.provider.normalize_order({"status": "handling"})


@override_settings(**VTEX_SETTINGS)
class ManualConnectTests(VtexTestMixin, APITestCase):
    URL = "/api/v1/integrations/vtex/connect-manual/"

    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")

    def _connect(self, **data):
        payload = {"account": "https://micuenta.myvtex.com/admin", "app_key": KEYS[0], "app_token": KEYS[1]}
        payload.update(data)
        return self.client.post(self.URL, payload, format="json", **auth_headers_for(self.user))

    def test_clave_valida_conecta_y_lanza_la_configuracion(self):
        response = self._connect()

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["warnings"], [])
        connection = StoreConnection.objects.get(pk=response.data["id"])
        self.assertEqual((connection.platform, connection.external_store_id), ("vtex", ACCOUNT))
        self.assertEqual(connection.store_url, "https://micuenta.myvtex.com")
        self.assertEqual(json.loads(connection.access_token), {"app_key": KEYS[0], "app_token": KEYS[1]})
        self.assertTrue(IntegrationEvent.objects.filter(connection=connection, event_type="internal/store_setup").exists())

    def test_clave_rechazada_no_conecta(self):
        response = self._connect(app_token="OTRO")
        self.assertEqual(response.status_code, 400)
        self.assertIn("401", response.data["detail"])
        self.assertFalse(StoreConnection.objects.exists())

    def test_sin_permiso_de_listar_pedidos_dice_que_recurso_falta(self):
        self.vtex.denied.add("/api/oms/pvt/orders")
        response = self._connect()
        self.assertEqual(response.status_code, 400)
        self.assertIn("List Orders", response.data["detail"])

    def test_sin_permiso_de_feed_conecta_con_un_aviso(self):
        self.vtex.denied.add("/api/orders/feed/config")
        response = self._connect()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIn("Feed v3 and Hook Admin", response.data["warnings"][0])

    def test_dominio_propio_no_sirve(self):
        response = self._connect(account="www.mitienda.com.ar")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.vtex.calls, [])

    def test_faltan_claves(self):
        self.assertEqual(self._connect(app_token="").status_code, 400)

    def test_no_hay_url_de_instalacion(self):
        response = self.client.get("/api/v1/integrations/vtex/install-url/", **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 404)


@override_settings(**VTEX_SETTINGS)
class SetupAndHookTests(VtexTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("comercio@example.com")
        self.connection = self._connection(owner=self.owner)

    def _delivery_url(self):
        return f"{BASE_URL}{WEBHOOKS_URL}?store={self.connection.pk}"

    def _post_hook(self, payload, *, key=None, store=None):
        self.connection.refresh_from_db()
        return self.client.post(
            f"{WEBHOOKS_URL}?store={store or self.connection.pk}",
            payload,
            format="json",
            HTTP_KEY=key if key is not None else self.connection.webhook_secret,
        )

    def test_configurar_crea_feed_y_hook_e_importa(self):
        self.vtex.orders = {
            "A-01": vtex_order("A-01", creationDate="2026-10-01T10:00:00+00:00"),
            "B-01": vtex_order("B-01", creationDate="2026-10-02T10:00:00+00:00"),
            "C-01": vtex_order("C-01", creationDate="2026-10-03T10:00:00+00:00", status="invoiced"),
            "D-01": vtex_order("D-01", status="payment-pending"),
        }

        enqueue_store_setup(self.connection)
        for _ in range(3):
            process_due_events()

        self.connection.refresh_from_db()
        self.assertEqual(self.connection.name, ACCOUNT)
        self.assertEqual(self.vtex.feed_config["filter"]["type"], "FromWorkflow")
        self.assertIn("ready-for-handling", self.vtex.feed_config["filter"]["status"])
        hook = self.vtex.hook_config["hook"]
        self.assertEqual(hook["url"], self._delivery_url())
        self.assertEqual(hook["headers"], {"key": self.connection.webhook_secret})
        self.assertEqual(self.connection.preferences["vtex_hook"], "ok")

        listed = self.vtex.calls_to("GET", "/api/oms/pvt/orders")[0]["params"]
        self.assertTrue(listed["f_creationDate"].startswith("creationDate:["))
        self.assertEqual(listed["f_status"], "ready-for-handling,handling,invoiced")
        # El pedido sin pagar no entra.
        self.assertEqual(sorted(Order.objects.values_list("external_id", flat=True)), ["A-01", "B-01", "C-01"])
        self.assertFalse(IntegrationEvent.objects.exclude(status=IntegrationEvent.Status.DONE).exists())

    def test_reconfigurar_no_repite_lo_que_ya_esta(self):
        provider = get_provider("vtex")
        url = f"{BASE_URL}{WEBHOOKS_URL}"
        self.assertEqual(provider.register_webhooks(self.connection, url, provider.webhook_events), ["feed", "hook"])
        self.assertEqual(provider.register_webhooks(self.connection, url, provider.webhook_events), [])

    def test_hook_de_otro_sistema_no_se_pisa(self):
        self.vtex.hook_config = {"filter": {"type": "FromWorkflow"}, "hook": {"url": "https://erp.example.com/vtex", "headers": {}}}
        provider = get_provider("vtex")

        provider.register_webhooks(self.connection, f"{BASE_URL}{WEBHOOKS_URL}", provider.webhook_events)

        self.assertEqual(self.vtex.hook_config["hook"]["url"], "https://erp.example.com/vtex")
        self.assertIsNotNone(self.vtex.feed_config)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.last_error, FOREIGN_HOOK_ERROR.format(minutes=5))
        self.assertEqual(self.connection.preferences["vtex_hook"], "foreign")

    def test_hook_que_vtex_no_acepta_no_frena_la_configuracion(self):
        self.vtex.hook_ping_ok = False
        provider = get_provider("vtex")

        changed = provider.register_webhooks(self.connection, f"{BASE_URL}{WEBHOOKS_URL}", provider.webhook_events)

        self.assertEqual(changed, ["feed"])
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.preferences["vtex_hook"], "error")

    def test_el_ping_se_contesta_sin_encolar(self):
        response = self.client.post(f"{WEBHOOKS_URL}?store={self.connection.pk}", {"hookConfig": "ping"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(IntegrationEvent.objects.exists())

    def test_aviso_con_el_secreto_de_la_tienda_trae_el_pedido(self):
        get_provider("vtex").ensure_webhook_secret(self.connection)
        self.vtex.orders = {"777-01": vtex_order("777-01")}

        response = self._post_hook(
            {"Domain": "Fulfillment", "OrderId": "777-01", "State": "ready-for-handling", "LastChange": "2026-10-01T12:00:00Z", "Origin": {"Account": ACCOUNT, "Key": KEYS[0]}}
        )
        process_due_events()

        self.assertEqual(response.status_code, 200)
        order = Order.objects.get(store_connection=self.connection, external_id="777-01")
        self.assertEqual(order.user, self.owner)
        self.assertEqual(IntegrationEvent.objects.get(event_type="order/status_changed").payload["id"], "777-01")

    def test_aviso_con_otro_secreto_se_rechaza(self):
        get_provider("vtex").ensure_webhook_secret(self.connection)
        response = self._post_hook({"OrderId": "777-01", "Origin": {"Account": ACCOUNT}}, key="otro")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(IntegrationEvent.objects.exists())

    def test_tienda_sin_secreto_todavia_no_acepta_avisos(self):
        response = self._post_hook({"OrderId": "777-01", "Origin": {"Account": ACCOUNT}}, key="cualquiera")
        self.assertEqual(response.status_code, 401)


@override_settings(**VTEX_SETTINGS)
class FeedReconciliationTests(VtexTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.connection = self._connection(owner=make_user("comercio@example.com"))

    def test_se_encola_con_el_intervalo_de_vtex(self):
        now = timezone.now()
        self.assertEqual(enqueue_due_reconciliations(now), 1)
        self.assertEqual(enqueue_due_reconciliations(now + timedelta(minutes=4)), 0)
        self.assertEqual(enqueue_due_reconciliations(now + timedelta(minutes=6)), 1)

    def test_sin_feed_lo_configura_aunque_no_haya_url_publica(self):
        with self.settings(INTEGRATIONS_PUBLIC_BASE_URL=""):
            enqueue_due_reconciliations()
            process_due_events()

        self.assertIsNotNone(self.vtex.feed_config)
        self.assertTrue(IntegrationEvent.objects.filter(event_type=RECONCILE_ORDERS_EVENT, status="done").exists())

    def test_trae_lo_del_feed_y_lo_confirma_despues_de_guardarlo(self):
        self.vtex.feed_config = {"filter": get_provider("vtex")._workflow_filter(), "queue": {}}
        self.vtex.hook_config = {"hook": {"url": f"{BASE_URL}{WEBHOOKS_URL}?store={self.connection.pk}", "headers": {}}}
        self.vtex.orders = {"5-01": vtex_order("5-01", status="canceled"), "6-01": vtex_order("6-01")}
        self.vtex.feed = [
            {"handle": "h1", "orderId": "5-01", "state": "canceled"},
            {"handle": "h2", "orderId": "6-01", "state": "ready-for-handling"},
            {"handle": "h3", "orderId": "6-01", "state": "handling"},
        ]

        enqueue_due_reconciliations()
        for _ in range(3):
            process_due_events()

        self.assertEqual(Order.objects.get(external_id="5-01").status, Order.Status.CANCELLED)
        self.assertTrue(Order.objects.filter(external_id="6-01").exists())
        # El pedido repetido en el lote se pide una sola vez.
        self.assertEqual(len(self.vtex.calls_to("GET", "/api/oms/pvt/orders/6-01")), 1)
        self.assertEqual(self.vtex.committed, ["h1", "h2", "h3"])
        self.assertEqual(self.vtex.feed, [])


@override_settings(**VTEX_SETTINGS)
class PushFulfillmentTests(VtexTestMixin, APITestCase):
    ORDER_ID = "900-01"

    def setUp(self):
        super().setUp()
        self.owner = make_user("comercio@example.com")
        self.connection = self._connection(owner=self.owner)
        self.vtex.orders = {self.ORDER_ID: vtex_order(self.ORDER_ID)}
        order, _ = upsert_store_order(self.connection, get_provider("vtex").normalize_order(vtex_order(self.ORDER_ID)))
        self.order = Order.objects.get(pk=order.pk)

    def _remote(self):
        return self.vtex.orders[self.ORDER_ID]

    def _ship(self, **data):
        data.setdefault("status", "dispatched")
        response = self.client.post(f"/api/v1/orders/{self.order.pk}/ship/", data, format="json", **auth_headers_for(self.owner))
        self.assertEqual(response.status_code, 200, response.data)
        process_due_events()

    def test_despachar_pone_el_seguimiento_en_la_factura(self):
        self._remote()["packageAttachment"]["packages"] = [invoice()]

        self._ship(carrier="Correo Argentino", tracking_number="AR123", tracking_url="https://seguimiento.example.com/AR123")

        self.assertEqual(self._remote()["status"], "handling")
        package = self._remote()["packageAttachment"]["packages"][0]
        self.assertEqual(package["trackingNumber"], "AR123")
        self.assertEqual(package["trackingUrl"], "https://seguimiento.example.com/AR123")
        self.assertEqual(package["courier"], "Correo Argentino")
        # Nunca se factura desde acá.
        self.assertEqual(self.vtex.calls_to("POST", f"/api/oms/pvt/orders/{self.ORDER_ID}/invoice"), [])

    def test_sin_factura_espera_y_lo_manda_cuando_llega(self):
        self._ship(tracking_number="AR123")

        push = IntegrationEvent.objects.get(event_type=PUSH_FULFILLMENT_EVENT)
        self.assertEqual(push.status, IntegrationEvent.Status.PENDING)
        self.assertIn("factura", push.last_error)

        # El ERP factura: llega el aviso de VTEX y el seguimiento sale solo.
        push.delete()
        self._remote().update(status="invoiced", packageAttachment={"packages": [invoice()]})
        get_provider("vtex").ensure_webhook_secret(self.connection)
        self.connection.refresh_from_db()
        self.client.post(
            f"{WEBHOOKS_URL}?store={self.connection.pk}",
            {"OrderId": self.ORDER_ID, "State": "invoiced", "Origin": {"Account": ACCOUNT}},
            format="json",
            HTTP_KEY=self.connection.webhook_secret,
        )
        process_due_events()
        process_due_events()

        self.assertEqual(self._remote()["packageAttachment"]["packages"][0]["trackingNumber"], "AR123")

    def test_el_mismo_seguimiento_no_se_repite(self):
        self._remote()["packageAttachment"]["packages"] = [invoice(trackingNumber="AR123")]
        self._ship(tracking_number="AR123")
        self.assertEqual(self.vtex.calls_to("PATCH", f"/api/oms/pvt/orders/{self.ORDER_ID}/invoice/0001-00001234"), [])

    def test_entregado_manda_el_evento_de_entrega(self):
        self._remote()["packageAttachment"]["packages"] = [invoice()]

        self._ship(status="delivered", tracking_number="AR123")

        package = self._remote()["packageAttachment"]["packages"][0]
        self.assertTrue(package["courierStatus"]["finished"])
        self.assertEqual(package["courierStatus"]["data"][0]["city"], "Córdoba")

    def test_pedido_cancelado_en_vtex_no_se_toca(self):
        self._remote()["status"] = "canceled"
        self._ship(tracking_number="AR123")
        touched = [call for call in self.vtex.calls if call["method"] != "GET"]
        self.assertEqual(touched, [])
