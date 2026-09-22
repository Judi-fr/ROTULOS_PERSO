"""Tests de la fase 4: al despachar un pedido de una tienda conectada, se le
informa a Tiendanube el estado y el número de seguimiento."""

from django.test import override_settings

from apps.orders.ingestion import create_order_from_data, upsert_store_order
from apps.orders.models import Order

from ..events import process_due_events
from ..models import IntegrationEvent, StoreConnection
from ..providers import get_provider
from ..stores import PUSH_FULFILLMENT_EVENT
from .test_store_integration_base import tiendanube_order
from .test_tiendanube_oauth import TEST_SETTINGS
from .test_tiendanube_sync import TiendanubeTestCase

FULFILLMENT_ORDERS = "orders/871254203/fulfillment-orders"
FULFILLMENT_ORDER = f"{FULFILLMENT_ORDERS}/fo-1"
TRACKING_CODE = "AR123456789"
TRACKING_URL = "https://www.correoargentino.com.ar/formularios/e-commerce?id=AR123456789"


class StoreOrderTestCase(TiendanubeTestCase):
    def setUp(self):
        super().setUp()
        provider = get_provider("tiendanube")
        order, _ = upsert_store_order(self.connection, provider.normalize_order(tiendanube_order()))
        self.order = Order.objects.get(pk=order.pk)

    def _push_events(self):
        return IntegrationEvent.objects.filter(event_type=PUSH_FULFILLMENT_EVENT)

    def _ship(self, status=Order.Status.DISPATCHED, tracking_number="", tracking_url=""):
        self.order.status = status
        self.order.tracking_number = tracking_number
        self.order.tracking_url = tracking_url
        self.order.save()


@override_settings(**TEST_SETTINGS)
class OrderShippingNotificationTests(StoreOrderTestCase):
    def test_despachar_un_pedido_de_tienda_encola_el_aviso(self):
        self._ship()

        event = self._push_events().get()
        self.assertEqual(event.payload, {"order_id": self.order.pk})
        self.assertEqual(event.resource_id, "871254203")
        self.assertEqual(event.connection, self.connection)

    def test_cambios_que_no_son_de_envio_no_avisan(self):
        self._ship(status=Order.Status.PREPARING)
        self.order.description = "Otra descripción"
        self.order.save()

        self.assertFalse(self._push_events().exists())

    def test_cargar_el_tracking_de_un_pedido_ya_despachado_avisa_de_nuevo(self):
        self._ship()
        self._push_events().update(status=IntegrationEvent.Status.DONE)

        self.order.tracking_number = TRACKING_CODE
        self.order.save()

        self.assertEqual(self._push_events().count(), 2)

    def test_pedidos_que_no_vienen_de_una_tienda_no_avisan(self):
        manual, _ = create_order_from_data(
            self.owner,
            {"destinatario": "Ana", "domicilio": "Calle 1", "ciudad": "La Plata"},
            source=Order.Source.MANUAL,
        )
        manual.status = Order.Status.DISPATCHED
        manual.save()

        self.assertFalse(self._push_events().exists())

    def test_tienda_desconectada_no_avisa(self):
        StoreConnection.objects.filter(pk=self.connection.pk).update(status=StoreConnection.Status.REVOKED)
        self.order = Order.objects.get(pk=self.order.pk)

        self._ship()

        self.assertFalse(self._push_events().exists())


@override_settings(**TEST_SETTINGS, INTEGRATIONS_EVENT_RETRY_BASE_SECONDS=60)
class PushFulfillmentTests(StoreOrderTestCase):
    def setUp(self):
        super().setUp()
        self.api.set("PATCH", FULFILLMENT_ORDER, 200, {})

    def _patches(self):
        return [call["json"] for call in self.api.calls_to("PATCH", FULFILLMENT_ORDER)]

    def test_despacho_con_tracking_actualiza_estado_y_seguimiento_con_aviso_al_comprador(self):
        self.api.set("GET", FULFILLMENT_ORDERS, 200, [{"id": "fo-1", "status": "UNPACKED"}])
        self._ship(tracking_number=TRACKING_CODE, tracking_url=TRACKING_URL)

        process_due_events()

        event = self._push_events().get()
        self.assertEqual(event.status, IntegrationEvent.Status.DONE, event.last_error)
        self.assertEqual(
            self._patches(),
            [
                {
                    "status": "DISPATCHED",
                    "tracking_info": {"code": TRACKING_CODE, "url": TRACKING_URL, "notify_customer": True},
                }
            ],
        )

    def test_despacho_sin_tracking_solo_cambia_el_estado(self):
        self.api.set("GET", FULFILLMENT_ORDERS, 200, [{"id": "fo-1", "status": "PACKED"}])
        self._ship()

        process_due_events()

        self.assertEqual(self._patches(), [{"status": "DISPATCHED"}])

    def test_entregado_se_informa_como_delivered(self):
        self.api.set("GET", FULFILLMENT_ORDERS, 200, [{"id": "fo-1", "status": "DISPATCHED"}])
        self._ship(status=Order.Status.DELIVERED)

        process_due_events()

        self.assertEqual(self._patches(), [{"status": "DELIVERED"}])

    def test_si_la_tienda_ya_tiene_ese_estado_y_tracking_no_hace_nada(self):
        self.api.set(
            "GET",
            FULFILLMENT_ORDERS,
            200,
            [{"id": "fo-1", "status": "DISPATCHED", "tracking_info": {"code": TRACKING_CODE}}],
        )
        self._ship(tracking_number=TRACKING_CODE)

        process_due_events()

        self.assertEqual(self._patches(), [])
        self.assertEqual(self._push_events().get().status, IntegrationEvent.Status.DONE)

    def test_tracking_nuevo_en_un_envio_ya_despachado_solo_actualiza_el_seguimiento(self):
        self.api.set(
            "GET",
            FULFILLMENT_ORDERS,
            200,
            [{"id": "fo-1", "status": "DISPATCHED", "tracking_info": {"code": "VIEJO-1"}}],
        )
        self._ship(tracking_number=TRACKING_CODE)

        process_due_events()

        self.assertEqual(self._patches(), [{"tracking_info": {"code": TRACKING_CODE, "notify_customer": True}}])

    def test_nunca_hace_retroceder_un_envio_mas_avanzado(self):
        self.api.set("GET", FULFILLMENT_ORDERS, 200, [{"id": "fo-1", "status": "DELIVERED"}])
        self._ship()

        process_due_events()

        self.assertEqual(self._patches(), [])

    def test_si_la_tienda_rechaza_el_cambio_falla_sin_reintentar(self):
        self.api.set("GET", FULFILLMENT_ORDERS, 200, [{"id": "fo-1", "status": "UNPACKED"}])
        self.api.set("PATCH", FULFILLMENT_ORDER, 422, {"description": "Invalid status transition"})
        self._ship()

        process_due_events()

        event = self._push_events().get()
        self.assertEqual(event.status, IntegrationEvent.Status.FAILED)
        self.assertEqual(event.attempts, 1)
        self.assertIn("Invalid status transition", event.last_error)

    def test_tiendanube_caida_se_reintenta(self):
        self.api.set("GET", FULFILLMENT_ORDERS, 503, {})
        self._ship()

        process_due_events()

        self.assertEqual(self._push_events().get().status, IntegrationEvent.Status.PENDING)

    def test_pedido_que_volvio_a_preparacion_antes_del_worker_no_avisa(self):
        self._ship()
        self.order.status = Order.Status.PREPARING
        self.order.save()

        process_due_events()

        self.assertEqual(self.api.calls, [])
        self.assertEqual(self._push_events().get().status, IntegrationEvent.Status.DONE)

    def test_pedido_sin_envios_para_informar(self):
        self.api.set("GET", FULFILLMENT_ORDERS, 200, [])
        self._ship()

        process_due_events()

        self.assertEqual(self._patches(), [])
        self.assertEqual(self._push_events().get().status, IntegrationEvent.Status.DONE)
