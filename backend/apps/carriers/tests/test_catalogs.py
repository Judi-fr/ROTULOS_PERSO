"""Catálogos globales del admin (T#354-356): transportistas manuales y
tamaños de rótulo. Leer, todos; escribir, solo ``catalogs.manage``."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit.models import AuditLog
from apps.carriers.models import ManualCarrier
from apps.labels.models import LabelSize
from apps.orders.models import Address, Order
from apps.accounts.tests.team_helpers import give_solo_teams

User = get_user_model()


def bearer(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(user).access_token}"}


class CatalogTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="admin@example.com", email="admin@example.com", password="Clave123!", is_staff=True)
        self.admin.groups.add(Group.objects.get_or_create(name="admin")[0])
        self.merchant = User.objects.create_user(username="tienda@example.com", email="tienda@example.com", password="Clave123!")
        self.merchant.groups.add(Group.objects.get_or_create(name="subscriber")[0])
        give_solo_teams()  # comercio "solo" (team_helpers, transitorio)

    def test_admin_manages_manual_carriers_and_merchants_only_read(self):
        url = "/api/v1/carriers/manual-carriers/"
        payload = {"name": "Via Cargo", "tracking_url_template": "https://viacargo.example/seguimiento?n={tracking}"}
        self.assertEqual(self.client.post(url, payload, format="json", **bearer(self.merchant)).status_code, 403)
        created = self.client.post(url, payload, format="json", **bearer(self.admin))
        self.assertEqual(created.status_code, 201, created.data)
        self.assertTrue(AuditLog.objects.filter(action="catalog.update", category="catalogs").exists())

        listed = self.client.get(url, **bearer(self.merchant)).data
        self.assertEqual([item["name"] for item in listed], ["Via Cargo"])

        self.client.delete(f"{url}{created.data['id']}/", **bearer(self.admin))
        self.assertEqual(self.client.get(url, **bearer(self.merchant)).data, [])
        all_items = self.client.get(f"{url}?include_inactive=1", **bearer(self.admin)).data
        self.assertFalse(all_items[0]["is_active"])
        # Un comerciante no ve las desactivadas aunque las pida.
        self.assertEqual(self.client.get(f"{url}?include_inactive=1", **bearer(self.merchant)).data, [])

    def test_manual_carrier_validation(self):
        url = "/api/v1/carriers/manual-carriers/"
        no_marker = self.client.post(url, {"name": "X", "tracking_url_template": "https://x.example/"}, format="json", **bearer(self.admin))
        self.assertIn("tracking_url_template", no_marker.data)
        api_carrier = self.client.post(url, {"name": "Andreani"}, format="json", **bearer(self.admin))
        self.assertIn("name", api_carrier.data)

    def test_shipping_with_a_catalog_carrier_fills_the_tracking_url(self):
        ManualCarrier.objects.create(name="Via Cargo", tracking_url_template="https://viacargo.example/seguimiento?n={tracking}")
        address = Address.objects.create(user=self.merchant, street="Corrientes", number="1", city="CABA")
        order = Order.objects.create(user=self.merchant, address=address)
        response = self.client.post(
            f"/api/v1/orders/{order.pk}/ship/",
            {"status": "dispatched", "carrier": "via cargo", "tracking_number": "AB 123"},
            format="json",
            **bearer(self.merchant),
        )
        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.carrier, "Via Cargo")
        self.assertEqual(order.tracking_url, "https://viacargo.example/seguimiento?n=AB%20123")

    def test_label_sizes_are_seeded_and_admin_only_to_edit(self):
        url = "/api/v1/labels/sizes/"
        names = [item["name"] for item in self.client.get(url, **bearer(self.merchant)).data]
        self.assertIn("Estándar 10 × 15", names)
        self.assertEqual(self.client.post(url, {"name": "Raro", "width_cm": 8, "height_cm": 8}, format="json", **bearer(self.merchant)).status_code, 403)
        too_big = self.client.post(url, {"name": "Enorme", "width_cm": 50, "height_cm": 8}, format="json", **bearer(self.admin))
        self.assertIn("width_cm", too_big.data)
        created = self.client.post(url, {"name": "Botella 8 × 12", "width_cm": "8", "height_cm": "12"}, format="json", **bearer(self.admin))
        self.assertEqual(created.status_code, 201, created.data)
        self.assertTrue(LabelSize.objects.filter(name="Botella 8 × 12").exists())
