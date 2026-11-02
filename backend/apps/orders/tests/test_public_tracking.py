"""La página pública de seguimiento: GET /api/v1/tracking/<token>/ sin sesión,
con el link firmado que lleva el QR del rótulo. Muestra el estado y el
transportista, nunca quién recibe ni la dirección."""

from rest_framework.test import APITestCase

from apps.accounts import teams
from apps.accounts.models import AccountSettings
from apps.accounts.tests.test_team_permissions import make_owner
from apps.orders.models import Address, Order
from apps.orders.public_tracking import tracking_token

URL = "/api/v1/tracking/{}/"


class PublicTrackingTests(APITestCase):
    def setUp(self):
        self.owner = make_owner("dueno@example.com")
        self.biz = teams.data_owner(self.owner)
        AccountSettings.objects.create(user=self.biz, sender_name="Bazar Las Lilas")
        self.address = Address.objects.create(
            user=self.biz, recipient_name="María Gómez", street="Colón", number="1234", city="Córdoba", state="Córdoba",
            postal_code="5000",
        )
        self.order = Order.objects.create(user=self.biz, address=self.address, external_number="501")

    def get(self, token):
        return self.client.get(URL.format(token))

    def test_muestra_estado_comercio_y_destino_sin_sesion(self):
        response = self.get(tracking_token(self.order))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["business"], "Bazar Las Lilas")
        self.assertEqual(response.data["number"], "501")
        self.assertEqual(response.data["status"], "created")
        self.assertEqual(response.data["destination"], "Córdoba, Córdoba")
        self.assertEqual([event["status"] for event in response.data["events"]], ["created"])

    def test_nunca_muestra_quien_recibe_ni_la_direccion(self):
        body = str(self.get(tracking_token(self.order)).data)
        for private in ("María", "Gómez", "Colón", "1234", "5000"):
            self.assertNotIn(private, body)

    def test_con_transportista_trae_el_numero_y_su_link(self):
        Order.objects.filter(pk=self.order.pk).update(
            carrier="Andreani", tracking_number="361793323593504",
            tracking_url="https://www.andreani.com/envio/361793323593504", status=Order.Status.DISPATCHED,
        )
        data = self.get(tracking_token(self.order)).data
        self.assertEqual(data["carrier"], "Andreani")
        self.assertEqual(data["tracking_number"], "361793323593504")
        self.assertEqual(data["tracking_url"], "https://www.andreani.com/envio/361793323593504")
        self.assertEqual(data["status_label"], "Despachado")

    def test_un_token_cambiado_o_inventado_es_404(self):
        token = tracking_token(self.order)
        self.assertEqual(self.get(token[:-2] + "xx").status_code, 404)
        self.assertEqual(self.get(str(self.order.pk)).status_code, 404)

    def test_pedido_anonimizado_o_borrado_es_404(self):
        token = tracking_token(self.order)
        self.order.delete()
        self.assertEqual(self.get(token).status_code, 404)

    def test_el_listado_trae_el_link_publico_que_abre_ese_pedido(self):
        from urllib.parse import parse_qs, urlparse

        from apps.accounts.tests.test_team_permissions import bearer

        rows = self.client.get("/api/v1/orders/", **bearer(self.owner)).data["results"]
        link = rows[0]["public_tracking_url"]
        self.assertIn("/seguimiento.html?t=", link)
        token = parse_qs(urlparse(link).query)["t"][0]
        self.assertEqual(self.get(token).data["number"], "501")
