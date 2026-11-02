"""El buscador de Mis pedidos: GET /orders/?search= por número, destinatario o
seguimiento, dentro de los pedidos del comercio actual."""

from rest_framework.test import APITestCase

from apps.accounts import teams
from apps.accounts.tests.test_team_permissions import bearer, make_owner
from apps.integrations.models import StoreConnection
from apps.orders.models import Address, Order

URL = "/api/v1/orders/"


class OrderSearchTests(APITestCase):
    def setUp(self):
        self.owner = make_owner("dueno@example.com")
        self.biz = teams.data_owner(self.owner)
        store = StoreConnection.objects.create(
            owner=self.biz, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="a", name="A"
        )
        maria = Address.objects.create(user=self.biz, recipient_name="María Gómez", street="Colón", number="1", city="Córdoba")
        juan = Address.objects.create(user=self.biz, recipient_name="Juan Pérez", street="Oroño", number="2", city="Rosario")
        self.maria = Order.objects.create(
            user=self.biz, address=maria, store_connection=store, external_id="9001", external_number="501"
        )
        self.juan = Order.objects.create(
            user=self.biz, address=juan, store_connection=store, external_id="9002", external_number="502",
            tracking_number="361793323593504",
        )
        other = make_owner("otro@example.com")
        other_biz = teams.data_owner(other)
        other_address = Address.objects.create(user=other_biz, recipient_name="María Otra", street="X", number="1", city="Y")
        Order.objects.create(user=other_biz, address=other_address, external_id="1", external_number="501")

    def found(self, search):
        response = self.client.get(URL, {"search": search}, **bearer(self.owner))
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return {row["id"] for row in response.data["results"]}

    def test_por_numero_con_o_sin_numeral(self):
        self.assertEqual(self.found("501"), {self.maria.pk})
        self.assertEqual(self.found("#502"), {self.juan.pk})

    def test_por_destinatario_sin_importar_mayusculas_y_todas_las_palabras(self):
        self.assertEqual(self.found("maría"), {self.maria.pk})
        self.assertEqual(self.found("juan pérez"), {self.juan.pk})
        self.assertEqual(self.found("juan gómez"), set())

    def test_por_numero_de_seguimiento_parcial(self):
        self.assertEqual(self.found("3593504"), {self.juan.pk})

    def test_no_trae_pedidos_de_otro_comercio(self):
        self.assertNotIn("María Otra", str(self.client.get(URL, {"search": "maría"}, **bearer(self.owner)).data))

    def test_vacio_no_filtra(self):
        self.assertEqual(self.found("  "), {self.maria.pk, self.juan.pk})
