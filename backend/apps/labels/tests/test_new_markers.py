"""Datos nuevos del rótulo (nombre, peso, bultos_pedido, transportista) y
``GET /api/v1/labels/preview-context/``."""

from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework.test import APITestCase

from apps.accounts.tests.team_helpers import new_business
from apps.accounts.tests.test_team_permissions import add_employee, make_owner
from apps.orders.models import Address, Order

from ..label_rendering import (
    SAMPLE_LABEL_CONTEXT,
    build_label_context,
    build_preview_context,
    build_shipment_context,
    first_name_of,
    format_weight_kg,
)

User = get_user_model()


class HelpersTests(TestCase):
    def test_nombre(self):
        self.assertEqual(first_name_of("  maría  josé "), "María")
        self.assertEqual(first_name_of("juan PÉREZ"), "Juan")
        self.assertEqual(first_name_of(""), "")
        self.assertEqual(first_name_of(None), "")

    def test_peso(self):
        self.assertEqual(format_weight_kg(Decimal("2.50")), "2,5 kg")
        self.assertEqual(format_weight_kg(Decimal("3")), "3 kg")
        self.assertEqual(format_weight_kg(Decimal("10.000")), "10 kg")
        self.assertEqual(format_weight_kg(Decimal("0.125")), "0,125 kg")
        self.assertEqual(format_weight_kg(Decimal("0.05")), "0,05 kg")
        self.assertEqual(format_weight_kg(Decimal("1000")), "1000 kg")
        self.assertEqual(format_weight_kg(None), "")
        self.assertEqual(format_weight_kg(Decimal("0")), "")


class OrderContextTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="c@example.com", email="c@example.com")

    def _order(self, **fields):
        address = Address.objects.create(user=self.owner, recipient_name="maría josé", street="Mitre", number="1")
        return Order.objects.create(user=self.owner, address=address, **fields)

    def test_pedido_real(self):
        context = build_label_context(
            order=self._order(package_count=3, carrier="OCA", total_weight_kg=Decimal("2.5"))
        )
        self.assertEqual(context["nombre"], "María")
        self.assertEqual(context["peso"], "2,5 kg")
        self.assertEqual(context["bultos_pedido"], "3")
        self.assertEqual(context["transportista"], "OCA")

    def test_sin_datos(self):
        context = build_label_context(order=self._order(package_count=0))
        self.assertEqual(context["peso"], "")
        self.assertEqual(context["bultos_pedido"], "")
        self.assertEqual(context["transportista"], "")

    def test_nombre_no_cae_al_comercio(self):
        address = Address.objects.create(user=self.owner, recipient_name="", street="Mitre", number="1")
        order = Order.objects.create(user=self.owner, address=address)
        context = build_label_context(order=order)
        self.assertEqual(context["nombre"], "")
        self.assertTrue(context["destinatario"])

    def test_peso_nulo_y_bultos_en_cero(self):
        context = build_label_context(order=self._order(package_count=0, total_weight_kg=None))
        self.assertEqual((context["peso"], context["bultos_pedido"]), ("", ""))

    def test_envio_de_tienda(self):
        shipment = SimpleNamespace(
            tracking_code="X1", order_number="5", external_label_id="1", tracking_url="", created_at=None,
            recipient_name="ana lópez", street="Mitre", number="1", reference="", city="", state="",
            postal_code="", country="", shipping_option="",
        )
        context = build_shipment_context(shipment)
        self.assertEqual(context["nombre"], "Ana")
        self.assertEqual(context["peso"], "")
        self.assertEqual(context["bultos_pedido"], "")
        self.assertEqual(context["transportista"], "")


class SampleContextTests(TestCase):
    def test_muestras(self):
        sample = build_preview_context()
        self.assertEqual(
            (sample["nombre"], sample["peso"], sample["bultos_pedido"], sample["transportista"]),
            ("María", "2,5 kg", "2", "Correo Argentino"),
        )
        self.assertEqual(
            (
                SAMPLE_LABEL_CONTEXT["nombre"],
                SAMPLE_LABEL_CONTEXT["peso"],
                SAMPLE_LABEL_CONTEXT["bultos_pedido"],
                SAMPLE_LABEL_CONTEXT["transportista"],
            ),
            ("María", "1,2 kg", "1", "Andreani"),
        )


class PreviewContextViewTests(APITestCase):
    URL = "/api/v1/labels/preview-context/"

    def test_devuelve_contexto_y_calculadas(self):
        user = User.objects.create_user(username="m@example.com", email="m@example.com", password="Clave123!")
        user.groups.add(Group.objects.get_or_create(name="subscriber")[0])
        new_business(user)
        self.client.force_authenticate(user=user)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["context"]["nombre"], "María")
        computed = response.data["computed"]
        for key in ("fecha", "hora", "fecha_hora", "secuencia", "bulto", "bultos", "bulto_de_bultos"):
            self.assertIn(key, computed)
            self.assertIsInstance(computed[key], str)
        self.assertEqual(
            (computed["bulto"], computed["bultos"], computed["bulto_de_bultos"]), ("1", "2", "1 de 2")
        )

    def test_sin_comercio(self):
        user = User.objects.create_user(username="x@example.com", email="x@example.com", password="Clave123!")
        self.client.force_authenticate(user=user)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data.get("code"), "no_team")

    def test_empleado_sin_acceso_a_imprimir(self):
        owner = make_owner("duenio@example.com")
        employee, _ = add_employee(owner, "emp@example.com", accesses=["orders.view"])
        self.client.force_authenticate(user=employee)
        response = self.client.get(self.URL)
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(response.data.get("code"), "no_team")

    def test_empleado_con_acceso_a_imprimir(self):
        owner = make_owner("duenio@example.com")
        employee, _ = add_employee(owner, "emp@example.com", accesses=["labels.print"])
        self.client.force_authenticate(user=employee)
        self.assertEqual(self.client.get(self.URL).status_code, 200)

    def test_sin_sesion(self):
        self.assertIn(self.client.get(self.URL).status_code, (401, 403))
