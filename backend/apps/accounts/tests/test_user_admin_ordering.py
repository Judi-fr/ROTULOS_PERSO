"""Orden de la lista de usuarios del admin por los encabezados de la tabla
(gestionuser.html): usuario, email, rol, estado, creado, pedidos y último pedido."""

from datetime import timedelta

from django.contrib.auth.models import Group
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.orders.models import Address, Order

from .team_helpers import new_business
from .test_team_permissions import bearer, make_user

URL = "/api/v1/users/"


class UserAdminOrderingTests(APITestCase):
    def setUp(self):
        self.admin = make_user("zeta-admin@example.com", group="admin")
        self.admin.is_staff = True
        self.admin.save()
        Group.objects.get_or_create(name="soporte")
        self.staff = make_user("soporte@example.com", group="soporte")
        self.merchant = make_user("bruno@example.com")
        self.merchant.first_name, self.merchant.last_name = "Bruno", "Ibarra"
        self.merchant.save()
        self.other = make_user("ana@example.com")
        self.other.first_name, self.other.last_name = "Ana", "Quiroga"
        self.other.is_active = False
        self.other.save()
        business = new_business(self.merchant)
        address = Address.objects.create(user=business, street="Calle", number="1", city="CABA")
        for _ in range(3):
            Order.objects.create(user=business, address=address)
        new_business(self.other)

    def emails(self, ordering):
        response = self.client.get(URL, {"ordering": ordering, "page_size": 50}, **bearer(self.admin))
        self.assertEqual(response.status_code, 200, response.data)
        return [row["email"] for row in response.data["results"]]

    def test_by_role_admin_then_platform_staff_then_merchants(self):
        emails = self.emails("role")
        self.assertLess(emails.index("zeta-admin@example.com"), emails.index("soporte@example.com"))
        self.assertLess(emails.index("soporte@example.com"), emails.index("bruno@example.com"))
        reverse = self.emails("-role")
        self.assertLess(reverse.index("bruno@example.com"), reverse.index("zeta-admin@example.com"))

    def test_by_status_inactive_first_when_ascending(self):
        emails = self.emails("status")
        self.assertEqual(emails[0], "ana@example.com")
        self.assertEqual(self.emails("-status")[-1], "ana@example.com")

    def test_by_name_is_what_the_user_column_shows(self):
        # Sin nombre se muestra el email: "soporte@..." va entre "Bruno" y "zeta-admin@...".
        emails = self.emails("name")
        order = [emails.index(email) for email in ("ana@example.com", "bruno@example.com", "soporte@example.com", "zeta-admin@example.com")]
        self.assertEqual(order, sorted(order))

    def test_by_orders_and_last_order_without_orders_go_last(self):
        self.assertEqual(self.emails("-orders_count")[0], "bruno@example.com")
        self.assertEqual(self.emails("-last_order_at")[0], "bruno@example.com")
        self.assertEqual(self.emails("last_order_at")[0], "bruno@example.com")

    def test_by_creation_date(self):
        type(self.merchant).objects.filter(pk=self.merchant.pk).update(date_joined=timezone.now() - timedelta(days=30))
        self.assertEqual(self.emails("date_joined")[0], "bruno@example.com")

    def test_unknown_field_keeps_the_default_order(self):
        self.assertEqual(self.emails("password"), self.emails(""))
