"""Tests de ``POST /api/v1/orders/<id>/ship/``: despachar un pedido propio
desde la web y cargar su seguimiento."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog

from ..models import Address, Order
from .test_orders import auth_headers_for

User = get_user_model()

TRACKING_URL = "https://www.correoargentino.com.ar/formularios/e-commerce?id=AR123456789"


class OrderShipTests(APITestCase):
    def setUp(self):
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(username="cliente1", email="cliente1@example.com", password="Clave123!")
        self.user.groups.add(subscriber)
        self.other = User.objects.create_user(username="cliente2", email="cliente2@example.com", password="Clave123!")
        self.other.groups.add(subscriber)
        self.address = Address.objects.create(user=self.user, street="Av. Siempre Viva", number="742", city="CABA")
        self.order = Order.objects.create(user=self.user, address=self.address)

    def _ship(self, payload, user=None, order=None):
        order = order or self.order
        return self.client.post(
            f"/api/v1/orders/{order.pk}/ship/", payload, format="json", **auth_headers_for(user or self.user)
        )

    def test_despachar_con_transportista_y_seguimiento(self):
        response = self._ship(
            {
                "status": "dispatched",
                "carrier": "Correo Argentino",
                "tracking_number": "AR123456789",
                "tracking_url": TRACKING_URL,
            }
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.DISPATCHED)
        self.assertEqual(self.order.carrier, "Correo Argentino")
        self.assertEqual(self.order.tracking_number, "AR123456789")
        self.assertEqual(self.order.tracking_url, TRACKING_URL)
        self.assertEqual([event["status"] for event in response.data["status_events"]], ["created", "dispatched"])

        log = AuditLog.objects.get(action="order.ship")
        self.assertEqual(log.actor, self.user)
        self.assertEqual(log.changes["status"], {"from": "created", "to": "dispatched"})
        # La vista registra su propia auditoría: sin duplicado sin actor.
        self.assertFalse(AuditLog.objects.filter(action="order.status_change").exists())

    def test_avanza_hasta_entregado_pero_nunca_vuelve_atras(self):
        self.assertEqual(self._ship({"status": "in_transit"}).status_code, 200)
        self.assertEqual(self._ship({"status": "delivered"}).status_code, 200)

        response = self._ship({"status": "dispatched"})

        self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.DELIVERED)

    def test_repetir_el_estado_actualiza_solo_el_seguimiento(self):
        self._ship({"status": "dispatched", "tracking_number": "VIEJO-1"})

        response = self._ship({"status": "dispatched", "tracking_number": "AR123456789"})

        self.assertEqual(response.status_code, 200, response.data)
        self.order.refresh_from_db()
        self.assertEqual(self.order.tracking_number, "AR123456789")
        self.assertEqual(self.order.status_events.count(), 2)

    def test_sin_cambios_no_audita_nada(self):
        self._ship({"status": "dispatched", "tracking_number": "AR1"})
        self._ship({"status": "dispatched", "tracking_number": "AR1"})

        self.assertEqual(AuditLog.objects.filter(action="order.ship").count(), 1)

    def test_pedido_cancelado_no_se_despacha(self):
        self.order.status = Order.Status.CANCELLED
        self.order.save()

        response = self._ship({"status": "dispatched"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("status", response.data)

    def test_solo_acepta_estados_de_envio_y_urls_validas(self):
        self.assertEqual(self._ship({"status": "preparing"}).status_code, 400)
        self.assertEqual(self._ship({"status": "cancelled"}).status_code, 400)
        self.assertEqual(self._ship({"status": "dispatched", "tracking_url": "no es una url"}).status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CREATED)

    def test_no_se_puede_despachar_un_pedido_ajeno(self):
        response = self._ship({"status": "dispatched"}, user=self.other)

        self.assertEqual(response.status_code, 404)

    def test_requiere_login(self):
        response = self.client.post(f"/api/v1/orders/{self.order.pk}/ship/", {"status": "dispatched"}, format="json")

        self.assertEqual(response.status_code, 401)

    def test_la_lista_indica_que_pedidos_se_pueden_despachar(self):
        response = self.client.get("/api/v1/orders/", **auth_headers_for(self.user))

        results = response.data["results"] if "results" in response.data else response.data
        self.assertTrue(results[0]["is_shippable"])
