"""Tests de los pedidos de Shopify: traducción del pedido GraphQL a
``NormalizedOrder``, avisos ``orders/*`` y la importación inicial paginada
por cursor. La API de Shopify se simula (``FakeShopify``)."""

import base64
import hashlib
import hmac
import json
from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from apps.orders.ingestion import upsert_store_order
from apps.orders.models import Order

from ..events import process_due_events
from ..models import IntegrationEvent
from ..providers import get_provider
from ..providers.shopify import split_street
from ..stores import PUSH_FULFILLMENT_EVENT, enqueue_initial_import
from .test_shopify_connection import SECRET, SHOP, SHOPIFY_SETTINGS, WEBHOOKS_URL, ShopifyTestMixin
from .test_tiendanube_oauth import auth_headers_for, make_user


def shopify_order(legacy_id, **overrides):
    """Pedido con la forma del fragmento ``OrderFields`` de la GraphQL Admin API."""
    order = {
        "id": f"gid://shopify/Order/{legacy_id}",
        "legacyResourceId": str(legacy_id),
        "name": f"#{1000 + int(legacy_id) % 1000}",
        "createdAt": "2026-09-30T15:00:00Z",
        "updatedAt": "2026-09-30T15:05:00Z",
        "cancelledAt": None,
        "displayFulfillmentStatus": "UNFULFILLED",
        "totalWeight": "750",
        "shippingLine": {"title": "Envío a domicilio"},
        "shippingAddress": {
            "name": "María Gómez",
            "address1": "Av. Corrientes 1234",
            "address2": "Piso 3 Depto B",
            "city": "CABA",
            "province": "Ciudad Autónoma de Buenos Aires",
            "zip": "C1043",
            "country": "Argentina",
            "countryCodeV2": "AR",
        },
        "billingAddress": {"name": "Juan Gómez"},
        "lineItems": {"nodes": [{"name": "Remera", "sku": "REM-1", "quantity": 2}, {"name": "Gorra", "sku": None, "quantity": 1}]},
    }
    order.update(overrides)
    return order


class ShopifyNormalizeOrderTests(TestCase):
    def setUp(self):
        self.provider = get_provider("shopify")

    def test_traduce_lo_que_necesita_el_rotulo(self):
        normalized = self.provider.normalize_order(shopify_order(5550001))

        self.assertEqual(normalized.external_id, "5550001")
        self.assertEqual(normalized.external_number, "1001")
        self.assertEqual(normalized.recipient_name, "María Gómez")
        self.assertEqual((normalized.street, normalized.number), ("Av. Corrientes", "1234"))
        self.assertEqual(normalized.reference, "Piso 3 Depto B")
        self.assertEqual((normalized.city, normalized.state), ("CABA", "Ciudad Autónoma de Buenos Aires"))
        self.assertEqual((normalized.postal_code, normalized.country), ("C1043", "Argentina"))
        self.assertEqual(normalized.shipping_option, "Envío a domicilio")
        self.assertEqual(normalized.total_weight_kg, Decimal("0.75"))
        self.assertEqual(normalized.description, "2x Remera, 1x Gorra")
        self.assertEqual(normalized.status, "")
        # Nunca se piden: el rótulo no los imprime (ver providers/shopify.py).
        self.assertEqual((normalized.contact_email, normalized.contact_phone), ("", ""))

    def test_sin_nombre_en_el_envio_usa_el_de_facturacion(self):
        address = dict(shopify_order(1)["shippingAddress"], name="")
        normalized = self.provider.normalize_order(shopify_order(1, shippingAddress=address))
        self.assertEqual(normalized.recipient_name, "Juan Gómez")

    def test_calle_y_numero(self):
        cases = [
            ("Av. Corrientes 1234", "AR", ("Av. Corrientes", "1234")),
            ("Calle 12 1500", "AR", ("Calle 12", "1500")),
            ("25 de Mayo 300", "AR", ("25 de Mayo", "300")),
            # Sin número: "25" es parte del nombre, no se parte.
            ("25 de Mayo", "AR", ("25 de Mayo", "")),
            ("Corrientes 1234 B", "AR", ("Corrientes", "1234B")),
            # Número adelante (Canadá/EE. UU.): queda entero, porque el rótulo
            # imprime calle + número y saldría "Victoria St 105".
            ("105 Victoria St", "CA", ("105 Victoria St", "")),
            ("Belgrano", "AR", ("Belgrano", "")),
        ]
        for address1, _country, expected in cases:
            self.assertEqual(split_street(address1), expected, address1)

    def test_estado_cancelado_y_despachado(self):
        cancelled = shopify_order(1, cancelledAt="2026-09-30T16:00:00Z")
        fulfilled = shopify_order(2, displayFulfillmentStatus="FULFILLED")

        self.assertEqual(self.provider.normalize_order(cancelled).status, "cancelled")
        self.assertEqual(self.provider.normalize_order(fulfilled).status, "dispatched")

    def test_sin_direccion_ni_peso_no_rompe(self):
        normalized = self.provider.normalize_order(
            shopify_order(3, shippingAddress=None, billingAddress=None, totalWeight="0")
        )
        self.assertEqual((normalized.street, normalized.recipient_name), ("", ""))
        self.assertIsNone(normalized.total_weight_kg)

    def test_sin_id_no_se_puede_identificar(self):
        with self.assertRaises(ValueError):
            self.provider.normalize_order({"name": "#1001"})


@override_settings(**SHOPIFY_SETTINGS)
class ShopifyOrderWebhookTests(ShopifyTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("comercio@example.com")
        self.connection = self._connection(owner=self.owner)

    def _post(self, topic, payload):
        body = json.dumps(payload).encode("utf-8")
        signature = base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()
        return self.client.generic(
            "POST",
            WEBHOOKS_URL,
            body,
            content_type="application/json",
            HTTP_X_SHOPIFY_HMAC_SHA256=signature,
            HTTP_X_SHOPIFY_TOPIC=topic,
            HTTP_X_SHOPIFY_SHOP_DOMAIN=SHOP,
        )

    def test_pedido_nuevo_entra_a_la_cuenta_del_duenio(self):
        self.fake.orders["gid://shopify/Order/777"] = shopify_order(777)

        response = self._post(
            "orders/create", {"id": 777, "updated_at": "2026-09-30T15:05:00Z", "email": "comprador@example.com"}
        )
        process_due_events()

        self.assertEqual(response.status_code, 200)
        order = Order.objects.get(store_connection=self.connection, external_id="777")
        self.assertEqual(order.user, self.owner)
        self.assertEqual(order.external_number, "1777")
        self.assertEqual(order.address.recipient_name, "María Gómez")
        self.assertEqual(order.source, Order.Source.STORE)

    def test_la_cola_no_guarda_los_datos_del_comprador(self):
        self._post("orders/create", {"id": 777, "email": "comprador@example.com", "shipping_address": {"name": "X"}})

        event = IntegrationEvent.objects.get(event_type="orders/create")
        self.assertEqual(event.resource_id, "777")
        self.assertNotIn("email", event.payload)
        self.assertNotIn("shipping_address", event.payload)

    def test_cancelado_en_shopify_cancela_el_pedido_local(self):
        self.fake.orders["gid://shopify/Order/777"] = shopify_order(777)
        self._post("orders/create", {"id": 777, "updated_at": "1"})
        process_due_events()

        self.fake.orders["gid://shopify/Order/777"] = shopify_order(
            777, cancelledAt="2026-09-30T16:00:00Z", updatedAt="2026-09-30T16:00:00Z"
        )
        self._post("orders/cancelled", {"id": 777, "updated_at": "2"})
        process_due_events()

        self.assertEqual(Order.objects.get(external_id="777").status, Order.Status.CANCELLED)

    def test_aviso_repetido_no_duplica(self):
        self.fake.orders["gid://shopify/Order/777"] = shopify_order(777)
        self._post("orders/create", {"id": 777, "updated_at": "1"})
        self._post("orders/updated", {"id": 777, "updated_at": "2"})
        process_due_events()

        self.assertEqual(Order.objects.filter(external_id="777").count(), 1)


@override_settings(**SHOPIFY_SETTINGS, SHOPIFY_ORDERS_PAGE_SIZE=2, INTEGRATIONS_INITIAL_IMPORT_DAYS=30)
class ShopifyInitialImportTests(ShopifyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.connection = self._connection(owner=make_user("comercio@example.com"))
        for legacy_id in (1, 2, 3):
            self.fake.orders[f"gid://shopify/Order/{legacy_id}"] = shopify_order(legacy_id)

    def _order_list_calls(self):
        return [call for call in self.fake.graphql_calls() if "query Orders(" in call["json"]["query"]]

    def test_importa_pagina_por_pagina_siguiendo_el_cursor(self):
        self.fake.order_pages = [["gid://shopify/Order/1", "gid://shopify/Order/2"], ["gid://shopify/Order/3"]]
        enqueue_initial_import(self.connection)

        process_due_events()  # página 1 -> encola la siguiente con su cursor
        process_due_events()  # página 2 -> última

        self.assertEqual(sorted(Order.objects.values_list("external_id", flat=True)), ["1", "2", "3"])
        calls = [call["json"]["variables"] for call in self._order_list_calls()]
        self.assertEqual([call["after"] for call in calls], [None, "cursor-1"])
        self.assertEqual(calls[0]["first"], 2)
        self.assertRegex(calls[0]["query"], r"^created_at:>=\d{4}-\d{2}-\d{2}$")
        self.assertFalse(IntegrationEvent.objects.exclude(status=IntegrationEvent.Status.DONE).exists())

    def test_reimportar_no_duplica(self):
        self.fake.order_pages = [["gid://shopify/Order/1"]]
        enqueue_initial_import(self.connection)
        process_due_events()
        enqueue_initial_import(self.connection)
        process_due_events()

        self.assertEqual(Order.objects.count(), 1)


OPEN_FULFILLMENT_ORDER = {
    "id": "gid://shopify/FulfillmentOrder/10",
    "status": "OPEN",
    "supportedActions": [{"action": "CREATE_FULFILLMENT"}, {"action": "HOLD"}],
    "fulfillments": {"nodes": []},
}


def closed_fulfillment_order(tracking_number):
    """Fulfillment order del comerciante ya despachado por la app."""
    tracking = [{"number": tracking_number, "url": None, "company": None}] if tracking_number else []
    return {
        "id": "gid://shopify/FulfillmentOrder/10",
        "status": "CLOSED",
        "supportedActions": [],
        "fulfillments": {"nodes": [{"id": "gid://shopify/Fulfillment/5", "status": "SUCCESS", "trackingInfo": tracking}]},
    }
TRACKING = "AR123456789"
TRACKING_URL = "https://www.correoargentino.com.ar/formularios/e-commerce?id=AR123456789"


@override_settings(**SHOPIFY_SETTINGS)
class ShopifyPushFulfillmentTests(ShopifyTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("comercio@example.com")
        self.connection = self._connection(owner=self.owner)
        order, _ = upsert_store_order(self.connection, get_provider("shopify").normalize_order(shopify_order(777)))
        self.order = Order.objects.get(pk=order.pk)

    def _ship(self, **data):
        data.setdefault("status", "dispatched")
        response = self.client.post(
            f"/api/v1/orders/{self.order.pk}/ship/", data, format="json", **auth_headers_for(self.owner)
        )
        self.assertEqual(response.status_code, 200, response.data)
        process_due_events()

    def test_despachar_crea_el_envio_en_shopify_con_tracking_y_correo(self):
        self.fake.fulfillment_orders = [OPEN_FULFILLMENT_ORDER]

        self._ship(carrier="Correo Argentino", tracking_number=TRACKING, tracking_url=TRACKING_URL)

        self.assertEqual(
            self.fake.mutations("fulfillmentCreate"),
            [
                {
                    "fulfillment": {
                        "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": "gid://shopify/FulfillmentOrder/10"}],
                        "notifyCustomer": True,
                        "trackingInfo": {"number": TRACKING, "url": TRACKING_URL, "company": "Correo Argentino"},
                    }
                }
            ],
        )
        self.assertEqual(IntegrationEvent.objects.get(event_type=PUSH_FULFILLMENT_EVENT).status, "done")

    def test_despachar_sin_tracking_igual_lo_marca_enviado(self):
        self.fake.fulfillment_orders = [OPEN_FULFILLMENT_ORDER]

        self._ship()

        created = self.fake.mutations("fulfillmentCreate")
        self.assertEqual(len(created), 1)
        self.assertNotIn("trackingInfo", created[0]["fulfillment"])

    def test_lo_que_despacha_un_servicio_externo_no_se_toca(self):
        # Con los scopes de la app, el fulfillment order de un servicio
        # externo ni aparece; y uno visible que no admite CREATE_FULFILLMENT
        # (en manos de otro) tampoco se despacha.
        self.fake.fulfillment_orders = [
            {
                "id": "gid://shopify/FulfillmentOrder/11",
                "status": "IN_PROGRESS",
                "supportedActions": [],
                "fulfillments": {"nodes": []},
            }
        ]

        self._ship(tracking_number=TRACKING)

        self.assertEqual(self.fake.mutations("fulfillmentCreate"), [])
        self.assertEqual(self.fake.mutations("fulfillmentTrackingInfoUpdate"), [])

    def test_tracking_nuevo_en_un_pedido_ya_enviado_se_actualiza(self):
        self.fake.fulfillment_orders = [closed_fulfillment_order("VIEJO")]

        self._ship(tracking_number=TRACKING)

        self.assertEqual(
            self.fake.mutations("fulfillmentTrackingInfoUpdate"),
            [
                {
                    "fulfillmentId": "gid://shopify/Fulfillment/5",
                    "trackingInfoInput": {"number": TRACKING},
                    "notifyCustomer": True,
                }
            ],
        )

    def test_despachado_sin_tracking_y_despues_se_carga(self):
        self.fake.fulfillment_orders = [closed_fulfillment_order("")]

        self._ship(carrier="Correo Argentino", tracking_number=TRACKING)

        self.assertEqual(self.fake.mutations("fulfillmentCreate"), [])
        self.assertEqual(
            self.fake.mutations("fulfillmentTrackingInfoUpdate")[0]["trackingInfoInput"],
            {"number": TRACKING, "company": "Correo Argentino"},
        )

    def test_mismo_tracking_no_se_reescribe(self):
        self.fake.fulfillment_orders = [closed_fulfillment_order(TRACKING)]

        self._ship(tracking_number=TRACKING)

        self.assertEqual(self.fake.mutations("fulfillmentTrackingInfoUpdate"), [])

    def test_shopify_rechaza_el_envio_y_no_se_reintenta(self):
        self.fake.fulfillment_orders = [OPEN_FULFILLMENT_ORDER]
        self.fake.mutation_errors = [{"field": ["fulfillment"], "message": "Fulfillment order is closed."}]

        self._ship(tracking_number=TRACKING)

        event = IntegrationEvent.objects.get(event_type=PUSH_FULFILLMENT_EVENT)
        self.assertEqual(event.status, "failed")
        self.assertIn("Fulfillment order is closed", event.last_error)

    def test_pedido_no_despachado_no_avisa(self):
        self.order.status = Order.Status.PREPARING
        self.order.save()

        self.assertFalse(IntegrationEvent.objects.filter(event_type=PUSH_FULFILLMENT_EVENT).exists())


def fulfilled_order(display_status):
    """Fulfillment order ya despachado, con su envío en ``display_status``."""
    order = closed_fulfillment_order(TRACKING)
    order["fulfillments"]["nodes"][0]["displayStatus"] = display_status
    return order


@override_settings(**SHOPIFY_SETTINGS)
class ShopifyDeliveryEventTests(ShopifyTestMixin, APITestCase):
    """En tránsito y entregado viajan como fulfillment events sobre el envío."""

    def setUp(self):
        super().setUp()
        self.owner = make_user("comercio@example.com")
        self.connection = self._connection(owner=self.owner)
        order, _ = upsert_store_order(self.connection, get_provider("shopify").normalize_order(shopify_order(777)))
        self.order = Order.objects.get(pk=order.pk)

    def _ship(self, status, **data):
        response = self.client.post(
            f"/api/v1/orders/{self.order.pk}/ship/",
            {"status": status, "tracking_number": TRACKING, **data},
            format="json",
            **auth_headers_for(self.owner),
        )
        self.assertEqual(response.status_code, 200, response.data)
        process_due_events()

    def _events(self):
        return [call["fulfillmentEvent"] for call in self.fake.mutations("fulfillmentEventCreate")]

    def test_en_transito_sobre_un_envio_existente(self):
        self.fake.fulfillment_orders = [fulfilled_order("FULFILLED")]

        self._ship("in_transit")

        self.assertEqual(self._events(), [{"fulfillmentId": "gid://shopify/Fulfillment/5", "status": "IN_TRANSIT"}])

    def test_entregado_sin_despacho_previo_crea_el_envio_y_lo_marca_entregado(self):
        self.fake.fulfillment_orders = [OPEN_FULFILLMENT_ORDER]

        self._ship("delivered")

        self.assertEqual(len(self.fake.mutations("fulfillmentCreate")), 1)
        # El id que devolvió fulfillmentCreate en el simulador.
        self.assertEqual(self._events(), [{"fulfillmentId": "gid://shopify/Fulfillment/1", "status": "DELIVERED"}])
        self.assertEqual(IntegrationEvent.objects.get(event_type=PUSH_FULFILLMENT_EVENT).status, "done")

    def test_ya_entregado_en_shopify_no_repite_el_evento(self):
        self.fake.fulfillment_orders = [fulfilled_order("DELIVERED")]
        self._ship("delivered")
        self.assertEqual(self._events(), [])

    def test_nunca_vuelve_un_envio_entregado_a_en_transito(self):
        self.fake.fulfillment_orders = [fulfilled_order("DELIVERED")]
        self._ship("in_transit")
        self.assertEqual(self._events(), [])

    def test_despachado_no_manda_eventos(self):
        self.fake.fulfillment_orders = [OPEN_FULFILLMENT_ORDER]
        self._ship("dispatched")
        self.assertEqual(self._events(), [])

    def test_tienda_que_no_aprobo_el_scope_queda_enviada_sin_fallar(self):
        self.connection.scopes = "read_orders,write_merchant_managed_fulfillment_orders"
        self.connection.save()
        self.fake.fulfillment_orders = [OPEN_FULFILLMENT_ORDER]

        self._ship("delivered")

        self.assertEqual(len(self.fake.mutations("fulfillmentCreate")), 1)
        self.assertEqual(self._events(), [])
        self.assertEqual(IntegrationEvent.objects.get(event_type=PUSH_FULFILLMENT_EVENT).status, "done")

    def test_shopify_rechaza_el_evento(self):
        self.fake.fulfillment_orders = [fulfilled_order("FULFILLED")]
        self.fake.mutation_errors = [{"field": ["fulfillmentEvent"], "message": "Fulfillment is cancelled."}]

        self._ship("delivered")

        event = IntegrationEvent.objects.get(event_type=PUSH_FULFILLMENT_EVENT)
        self.assertEqual(event.status, "failed")
        self.assertIn("Fulfillment is cancelled", event.last_error)
