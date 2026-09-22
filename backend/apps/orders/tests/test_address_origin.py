"""La agenda "Mis direcciones" (``/api/v1/addresses/``) solo lista las
direcciones que el usuario guardó a mano: las que crea un pedido de tienda,
una importación o la API son de un comprador (``Address.Origin.SHIPMENT``)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from apps.orders.ingestion import create_order_from_data
from apps.orders.models import Address, Order

from .test_orders import auth_headers_for

User = get_user_model()


class AddressOriginTests(APITestCase):
    def setUp(self):
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(
            username="vendedor3@example.com", email="vendedor3@example.com", password="Clave123!"
        )
        self.user.groups.add(subscriber)
        self.own = Address.objects.create(
            user=self.user, label="Depósito", street="Corrientes", number="1000", city="CABA"
        )
        self.shipment = Address.objects.create(
            user=self.user,
            origin=Address.Origin.SHIPMENT,
            recipient_name="María Gómez",
            street="Cabildo",
            number="2000",
            city="CABA",
        )

    def test_la_agenda_solo_lista_las_propias(self):
        response = self.client.get("/api/v1/addresses/", **auth_headers_for(self.user))

        self.assertEqual(response.status_code, 200)
        ids = [address["id"] for address in response.data["results"]]
        self.assertEqual(ids, [self.own.pk])

    def test_una_direccion_de_envio_no_se_puede_leer_ni_borrar_desde_la_agenda(self):
        headers = auth_headers_for(self.user)

        self.assertEqual(
            self.client.get(f"/api/v1/addresses/{self.shipment.pk}/", **headers).status_code, 404
        )
        self.assertEqual(
            self.client.delete(f"/api/v1/addresses/{self.shipment.pk}/", **headers).status_code, 404
        )
        self.assertTrue(Address.objects.filter(pk=self.shipment.pk).exists())

    def test_una_direccion_creada_desde_la_agenda_queda_como_propia(self):
        response = self.client.post(
            "/api/v1/addresses/",
            {"label": "Casa", "street": "Rivadavia", "city": "CABA"},
            **auth_headers_for(self.user),
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(Address.objects.get(pk=response.data["id"]).origin, Address.Origin.OWN)

    def test_un_pedido_importado_crea_una_direccion_de_envio(self):
        order, created = create_order_from_data(
            self.user,
            {
                "destinatario": "Juan Pérez",
                "domicilio": "Santa Fe",
                "numero": "3000",
                "ciudad": "CABA",
                "external_id": "imp-1",
            },
            source=Order.Source.IMPORT,
        )

        self.assertTrue(created)
        self.assertEqual(order.address.origin, Address.Origin.SHIPMENT)
        # Y no aparece en la agenda del usuario.
        response = self.client.get("/api/v1/addresses/", **auth_headers_for(self.user))
        self.assertNotIn(order.address.pk, [address["id"] for address in response.data["results"]])

    def test_el_pedido_sigue_mostrando_su_direccion(self):
        order = Order.objects.create(user=self.user, address=self.shipment)

        response = self.client.get(f"/api/v1/orders/{order.pk}/", **auth_headers_for(self.user))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["address"]["id"], self.shipment.pk)
