"""Pedidos de ejemplo (/orders/sample/): un comercio nuevo prueba imprimir un
rótulo sin conectar nada, y después los borra sin tocar sus pedidos reales."""

from rest_framework.test import APITestCase

from apps.accounts import teams
from apps.accounts.tests.test_team_permissions import add_employee, bearer, make_owner
from apps.carriers.models import CarrierAccount, CarrierShipment
from apps.orders.models import Address, Order

URL = "/api/v1/orders/sample/"


class SampleOrderTests(APITestCase):
    def setUp(self):
        self.owner = make_owner("dueno@example.com")
        self.biz = teams.data_owner(self.owner)

    def create(self, user=None):
        return self.client.post(URL, **bearer(user or self.owner))

    def test_crea_pedidos_marcados_como_ejemplo(self):
        response = self.create()
        self.assertEqual(response.status_code, 201, response.data)
        samples = Order.objects.filter(user=self.biz, is_sample=True)
        self.assertEqual(response.data["count"], samples.count())
        self.assertGreaterEqual(samples.count(), 3)
        for order in samples:
            self.assertEqual(order.status, Order.Status.CREATED)
            self.assertTrue(order.address.recipient_name)
            self.assertEqual(order.address.origin, Address.Origin.SHIPMENT)

    def test_crearlos_dos_veces_no_los_duplica(self):
        first = self.create().data["count"]
        self.assertEqual(self.create().status_code, 200)
        self.assertEqual(Order.objects.filter(user=self.biz, is_sample=True).count(), first)

    def test_get_dice_cuantos_hay(self):
        self.assertEqual(self.client.get(URL, **bearer(self.owner)).data["count"], 0)
        self.create()
        self.assertGreater(self.client.get(URL, **bearer(self.owner)).data["count"], 0)

    def test_el_listado_dice_que_son_de_ejemplo(self):
        self.create()
        rows = self.client.get("/api/v1/orders/", **bearer(self.owner)).data["results"]
        self.assertTrue(rows and all(row["is_sample"] for row in rows))

    def test_borrarlos_no_toca_los_pedidos_reales(self):
        address = Address.objects.create(user=self.biz, recipient_name="Real", street="X", number="1", city="Y")
        real = Order.objects.create(user=self.biz, address=address)
        self.create()
        response = self.client.delete(URL, **bearer(self.owner))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Order.objects.filter(user=self.biz, is_sample=True).exists())
        self.assertTrue(Order.objects.filter(pk=real.pk).exists())

    def test_uno_con_envio_del_transportista_no_se_borra(self):
        self.create()
        sample = Order.objects.filter(user=self.biz, is_sample=True).first()
        account = CarrierAccount.objects.create(owner=self.biz, carrier="andreani")
        CarrierShipment.objects.create(account=account, order=sample, carrier="andreani", contract="400006709", tracking_number="1")
        response = self.client.delete(URL, **bearer(self.owner))
        self.assertEqual(response.data["kept"], 1)
        self.assertTrue(Order.objects.filter(pk=sample.pk).exists())

    def test_otro_comercio_no_ve_ni_borra_los_ejemplos(self):
        self.create()
        other = make_owner("otro@example.com")
        self.assertEqual(self.client.get(URL, **bearer(other)).data["count"], 0)
        self.client.delete(URL, **bearer(other))
        self.assertTrue(Order.objects.filter(user=self.biz, is_sample=True).exists())

    def test_empleado_sin_cargar_pedidos_no_puede(self):
        employee, _ = add_employee(self.owner, "empleado@example.com", accesses=["orders.view"])
        self.assertEqual(self.create(employee).status_code, 403)
        self.assertEqual(self.client.delete(URL, **bearer(employee)).status_code, 403)

    def test_un_envio_cancelado_no_retiene_el_ejemplo(self):
        self.create()
        sample = Order.objects.filter(user=self.biz, is_sample=True).first()
        account = CarrierAccount.objects.create(owner=self.biz, carrier="andreani")
        CarrierShipment.objects.create(
            account=account, order=sample, carrier="andreani", contract="400006709", tracking_number="1",
            status=CarrierShipment.Status.CANCELLED,
        )
        response = self.client.delete(URL, **bearer(self.owner))
        self.assertEqual(response.data["kept"], 0)
        self.assertFalse(Order.objects.filter(pk=sample.pk).exists())
