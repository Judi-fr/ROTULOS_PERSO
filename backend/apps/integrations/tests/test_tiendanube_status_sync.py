"""Tests de la sincronización del estado de un pedido cuando cambia en
Tiendanube (cancelado, enviado, entregado), y del despacho desde la web
avisándole a la tienda."""

from django.test import override_settings

from apps.orders.ingestion import upsert_store_order
from apps.orders.models import Order

from ..events import enqueue_event, process_due_events
from ..models import IntegrationEvent
from ..providers import get_provider
from ..stores import PUSH_FULFILLMENT_EVENT
from .test_store_integration_base import tiendanube_order
from .test_tiendanube_oauth import TEST_SETTINGS, auth_headers_for
from .test_tiendanube_sync import TiendanubeTestCase

LATER = "2026-09-10T15:00:00+0000"
EARLIER = "2026-09-10T12:00:00+0000"


@override_settings(**TEST_SETTINGS)
class StoreStatusSyncTests(TiendanubeTestCase):
    def setUp(self):
        super().setUp()
        self.provider = get_provider("tiendanube")

    def _upsert(self, **overrides):
        order, result = upsert_store_order(self.connection, self.provider.normalize_order(tiendanube_order(**overrides)))
        return Order.objects.get(pk=order.pk), result

    def _push_events(self):
        return IntegrationEvent.objects.filter(event_type=PUSH_FULFILLMENT_EVENT)

    def test_cancelado_en_la_tienda_cancela_el_pedido(self):
        self._upsert()

        order, result = self._upsert(status="cancelled", updated_at=LATER)

        self.assertEqual(result, "updated")
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(order.status_events.last().status, Order.Status.CANCELLED)

    def test_enviado_en_la_tienda_marca_despachado_sin_avisarle_de_vuelta(self):
        self._upsert()

        order, _ = self._upsert(shipping_status="shipped", updated_at=LATER)

        self.assertEqual(order.status, Order.Status.DISPATCHED)
        self.assertFalse(self._push_events().exists())

    def test_despues_de_sincronizar_un_cambio_local_si_avisa(self):
        self._upsert()
        order, _ = self._upsert(shipping_status="shipped", updated_at=LATER)

        order.tracking_number = "AR123456789"
        order.save()

        self.assertTrue(self._push_events().exists())

    def test_entregado_en_la_tienda(self):
        self._upsert()

        order, _ = self._upsert(shipping_status="delivered", updated_at=LATER)

        self.assertEqual(order.status, Order.Status.DELIVERED)

    def test_nunca_hace_retroceder_un_estado_local_mas_avanzado(self):
        order, _ = self._upsert()
        order.status = Order.Status.DELIVERED
        order.save()

        order, _ = self._upsert(shipping_status="shipped", updated_at=LATER)

        self.assertEqual(order.status, Order.Status.DELIVERED)

    def test_un_pedido_cancelado_localmente_no_se_reactiva(self):
        order, _ = self._upsert()
        order.status = Order.Status.CANCELLED
        order.save()

        order, _ = self._upsert(shipping_status="shipped", updated_at=LATER)

        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_un_aviso_viejo_no_cambia_el_estado(self):
        self._upsert()

        order, result = self._upsert(status="cancelled", updated_at=EARLIER)

        self.assertEqual(result, "unchanged")
        self.assertEqual(order.status, Order.Status.CREATED)

    def test_pedido_que_entra_ya_cancelado(self):
        order, result = self._upsert(status="cancelled")

        self.assertEqual(result, "created")
        self.assertEqual(order.status, Order.Status.CANCELLED)

    def test_webhook_de_cancelacion_de_punta_a_punta(self):
        self._upsert()
        self.api.set("GET", "orders/871254203", 200, tiendanube_order(status="cancelled", updated_at=LATER))
        event, _ = enqueue_event(
            platform="tiendanube",
            event_type="order/cancelled",
            connection=self.connection,
            resource_id="871254203",
            payload={"store_id": 555, "event": "order/cancelled", "id": 871254203},
        )

        process_due_events()

        event.refresh_from_db()
        self.assertEqual(event.status, IntegrationEvent.Status.DONE, event.last_error)
        self.assertEqual(Order.objects.get().status, Order.Status.CANCELLED)
        self.assertFalse(self._push_events().exists())

    def test_despachar_desde_la_web_le_avisa_a_la_tienda(self):
        order, _ = self._upsert()

        response = self.client.post(
            f"/api/v1/orders/{order.pk}/ship/",
            {"status": "dispatched", "tracking_number": "AR123456789"},
            format="json",
            **auth_headers_for(self.owner),
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._push_events().get().payload, {"order_id": order.pk})
