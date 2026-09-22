"""Tests de los webhooks de privacidad obligatorios de Tiendanube
(``customers/redact``, ``store/redact``, ``customers/data_request``)."""

import hashlib
import hmac
import json

from django.core import mail
from django.test import override_settings

from apps.audit.models import AuditLog
from apps.orders.ingestion import upsert_store_order
from apps.orders.models import Order

from ..events import enqueue_event, process_due_events
from ..models import IntegrationEvent, StoreConnection
from ..privacy import REDACTED
from ..providers import get_provider
from .test_store_integration_base import tiendanube_order
from .test_tiendanube_oauth import TEST_SETTINGS
from .test_tiendanube_sync import WEBHOOKS_URL, TiendanubeTestCase

CUSTOMER = {"id": 9, "email": "juan@example.com", "phone": "+54 11 5555-0000", "identification": "30111222"}


@override_settings(**TEST_SETTINGS, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class TiendanubePrivacyTests(TiendanubeTestCase):
    def setUp(self):
        super().setUp()
        provider = get_provider("tiendanube")
        juan = tiendanube_order(id=1, contact_email="juan@example.com")
        ana = tiendanube_order(id=2, contact_email="ana@example.com")
        ana["shipping_address"] = {**ana["shipping_address"], "name": "Ana Gómez"}
        self.juan_order, _ = upsert_store_order(self.connection, provider.normalize_order(juan))
        self.ana_order, _ = upsert_store_order(self.connection, provider.normalize_order(ana))

    def _process(self, event_type, payload, connection="default"):
        event, _ = enqueue_event(
            platform="tiendanube",
            event_type=event_type,
            connection=self.connection if connection == "default" else connection,
            payload=payload,
        )
        process_due_events()
        event.refresh_from_db()
        return event

    def _assert_anonymized(self, order):
        order = Order.objects.select_related("address").get(pk=order.pk)
        self.assertEqual(order.address.recipient_name, REDACTED)
        self.assertEqual(order.address.street, REDACTED)
        self.assertEqual(order.address.number, "")
        self.assertEqual(order.contact_email, "")
        self.assertEqual(order.contact_phone, "")
        self.assertEqual(order.raw_payload, {})

    def test_customers_redact_anonimiza_solo_los_pedidos_del_comprador(self):
        event = self._process(
            "customers/redact", {"store_id": 555, "customer": CUSTOMER, "orders_to_redact": [1]}
        )

        self.assertEqual(event.status, IntegrationEvent.Status.DONE, event.last_error)
        self._assert_anonymized(self.juan_order)
        ana = Order.objects.select_related("address").get(pk=self.ana_order.pk)
        self.assertEqual(ana.address.recipient_name, "Ana Gómez")
        self.assertEqual(ana.contact_email, "ana@example.com")
        # Los datos del comprador que traía el aviso tampoco quedan guardados.
        self.assertNotIn("customer", event.payload)
        self.assertNotIn("juan@example.com", json.dumps(event.payload))
        log = AuditLog.objects.get(action="privacy.customer_redact")
        self.assertEqual(log.changes["orders_redacted"]["to"], 1)
        self.assertNotIn("juan", json.dumps(log.changes).lower())

    def test_customers_redact_tambien_encuentra_los_pedidos_por_email(self):
        self._process("customers/redact", {"store_id": 555, "customer": CUSTOMER, "orders_to_redact": []})

        self._assert_anonymized(self.juan_order)

    def test_store_redact_desconecta_y_anonimiza_toda_la_tienda(self):
        enqueue_event(platform="tiendanube", event_type="order/updated", connection=self.connection,
                      resource_id="1", payload={"store_id": 555, "id": 1})
        IntegrationEvent.objects.update(status=IntegrationEvent.Status.DONE)

        event = self._process("store/redact", {"store_id": 555})

        self.assertEqual(event.status, IntegrationEvent.Status.DONE, event.last_error)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status, StoreConnection.Status.REVOKED)
        self.assertEqual(self.connection.access_token, "")
        self.assertEqual(self.connection.name, "")
        self._assert_anonymized(self.juan_order)
        self._assert_anonymized(self.ana_order)
        self.assertFalse(IntegrationEvent.objects.filter(connection=self.connection).exclude(payload={}).exists())
        self.assertTrue(AuditLog.objects.filter(action="privacy.store_redact").exists())

    def test_store_redact_llega_despues_de_desinstalar(self):
        self.connection.status = StoreConnection.Status.REVOKED
        self.connection.save()

        event = self._process("store/redact", {"store_id": 555})

        self.assertEqual(event.status, IntegrationEvent.Status.DONE, event.last_error)
        self._assert_anonymized(self.juan_order)

    def test_data_request_le_manda_el_reporte_al_comerciante(self):
        event = self._process(
            "customers/data_request",
            {"store_id": 555, "customer": CUSTOMER, "orders_requested": [1], "data_request": {"id": 456}},
        )

        self.assertEqual(event.status, IntegrationEvent.Status.DONE, event.last_error)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, [self.owner.email])
        filename, content, mimetype = message.attachments[0]
        self.assertEqual(mimetype, "application/json")
        report = json.loads(content)
        self.assertEqual(report["data_request_id"], 456)
        self.assertEqual([order["external_id"] for order in report["orders"]], ["1"])
        self.assertEqual(report["orders"][0]["recipient_name"], "Juan Pérez")
        self.assertEqual(event.result["orders"][0]["external_id"], "1")
        self.assertTrue(AuditLog.objects.filter(action="privacy.data_request").exists())

    def test_data_request_de_una_tienda_sin_cuenta_queda_guardado_para_enviar_a_mano(self):
        unclaimed = StoreConnection.objects.create(platform="tiendanube", external_store_id="777")

        event = self._process(
            "customers/data_request", {"store_id": 777, "customer": CUSTOMER, "orders_requested": []},
            connection=unclaimed,
        )

        self.assertEqual(event.status, IntegrationEvent.Status.FAILED)
        self.assertIn("a mano", event.last_error)
        self.assertIn("orders", event.result)
        self.assertEqual(mail.outbox, [])

    def test_avisos_de_privacidad_de_una_tienda_desconocida(self):
        redact = self._process("store/redact", {"store_id": 999}, connection=None)
        customer = self._process("customers/redact", {"store_id": 999, "customer": CUSTOMER, "orders_to_redact": [1]},
                                 connection=None)

        self.assertEqual(redact.status, IntegrationEvent.Status.DONE)
        self.assertEqual(customer.status, IntegrationEvent.Status.DONE)
        self.assertNotIn("customer", customer.payload)

    def test_el_receptor_acepta_los_webhooks_de_privacidad(self):
        body = json.dumps({"store_id": 555, "event": "store/redact"}).encode("utf-8")
        signature = hmac.new(b"secreto-de-prueba", body, hashlib.sha256).hexdigest()

        response = self.client.generic(
            "POST", WEBHOOKS_URL, body, content_type="application/json", HTTP_X_LINKEDSTORE_HMAC_SHA256=signature
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(IntegrationEvent.objects.filter(event_type="store/redact", connection=self.connection).exists())
