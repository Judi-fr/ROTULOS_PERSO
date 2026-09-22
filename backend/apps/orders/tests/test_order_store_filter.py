"""Tests del filtro ``GET /api/v1/orders/?store=``: un vendedor con varias
tiendas conectadas ve los pedidos de una sola, o solo los cargados a mano."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from apps.integrations.models import StoreConnection

from ..models import Address, Order
from .test_orders import auth_headers_for

User = get_user_model()


class OrderStoreFilterTests(APITestCase):
    def setUp(self):
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(username="vendedor1", email="vendedor1@example.com", password="Clave123!")
        self.user.groups.add(subscriber)
        self.other = User.objects.create_user(username="vendedor2", email="vendedor2@example.com", password="Clave123!")
        self.other.groups.add(subscriber)

        self.store_a = StoreConnection.objects.create(
            owner=self.user, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="111", name="Tienda A"
        )
        self.store_b = StoreConnection.objects.create(
            owner=self.user, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="222", name="Tienda B"
        )
        self.foreign_store = StoreConnection.objects.create(
            owner=self.other, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="333", name="Ajena"
        )

        address = Address.objects.create(user=self.user, street="Calle", number="1", city="CABA")
        self.order_a = Order.objects.create(user=self.user, address=address, store_connection=self.store_a, external_id="a1")
        self.order_b = Order.objects.create(user=self.user, address=address, store_connection=self.store_b, external_id="b1")
        self.manual = Order.objects.create(user=self.user, address=address)

        other_address = Address.objects.create(user=self.other, street="Otra", number="2", city="CABA")
        Order.objects.create(user=self.other, address=other_address, store_connection=self.foreign_store, external_id="x1")

    def _list_ids(self, query=""):
        response = self.client.get(f"/api/v1/orders/{query}", **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 200, response.data)
        return {order["id"] for order in response.data["results"]}

    def test_sin_filtro_devuelve_todos_los_pedidos_propios(self):
        self.assertEqual(self._list_ids(), {self.order_a.pk, self.order_b.pk, self.manual.pk})

    def test_filtra_por_tienda(self):
        self.assertEqual(self._list_ids(f"?store={self.store_a.pk}"), {self.order_a.pk})
        self.assertEqual(self._list_ids(f"?store={self.store_b.pk}"), {self.order_b.pk})

    def test_filtra_pedidos_sin_tienda(self):
        self.assertEqual(self._list_ids("?store=manual"), {self.manual.pk})

    def test_tienda_ajena_no_expone_pedidos(self):
        self.assertEqual(self._list_ids(f"?store={self.foreign_store.pk}"), set())

    def test_valor_invalido_devuelve_400(self):
        response = self.client.get("/api/v1/orders/?store=abc", **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 400)

    def test_cada_pedido_trae_el_nombre_de_su_tienda(self):
        response = self.client.get(f"/api/v1/orders/?store={self.store_a.pk}", **auth_headers_for(self.user))
        self.assertEqual(response.data["results"][0]["store_name"], "Tienda A")


class OrderStatusFilterTests(APITestCase):
    """``GET /api/v1/orders/?status=`` — uno o varios estados separados por
    coma; lo usa la pantalla de impresión de rótulos para trabajar sobre los
    pedidos pendientes."""

    def setUp(self):
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(
            username="vendedor9@example.com", email="vendedor9@example.com", password="Clave123!"
        )
        self.user.groups.add(subscriber)
        address = Address.objects.create(user=self.user, street="Calle", number="1", city="CABA")
        self.created = Order.objects.create(user=self.user, address=address)
        self.dispatched = Order.objects.create(user=self.user, address=address)
        self.dispatched.status = Order.Status.DISPATCHED
        self.dispatched.save(update_fields=["status"])

    def _ids(self, query):
        response = self.client.get(f"/api/v1/orders/{query}", **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 200, response.data)
        return {order["id"] for order in response.data["results"]}

    def test_filtra_por_un_estado(self):
        self.assertEqual(self._ids("?status=created"), {self.created.pk})

    def test_filtra_por_varios_estados(self):
        self.assertEqual(
            self._ids("?status=created,dispatched"), {self.created.pk, self.dispatched.pk}
        )

    def test_estado_desconocido_devuelve_400(self):
        response = self.client.get(
            "/api/v1/orders/?status=inventado", **auth_headers_for(self.user)
        )
        self.assertEqual(response.status_code, 400)
