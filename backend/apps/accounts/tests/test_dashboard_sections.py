"""El panel agrupado (2026-10-20): seis secciones en vez de un botón por
pantalla, cada una con sus accesos adentro, y los primeros pasos de un
comercio nuevo."""

from rest_framework.test import APITestCase

from apps.accounts.models import AccountSettings
from apps.carriers.models import CarrierAccount
from apps.integrations.models import StoreConnection
from apps.labels.models import LabelTemplate

from .test_team_permissions import add_employee, bearer, make_user

URL = "/api/v1/auth/users/me/dashboard/"


def sections(client, user):
    return {section["key"]: section for section in client.get(URL, **bearer(user)).data["sections"]}


class DashboardSectionTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")

    def test_owner_gets_six_sections_with_their_links(self):
        data = sections(self.client, self.owner)
        self.assertEqual(list(data), ["orders", "print", "design", "stores", "business", "help"])
        self.assertEqual(data["orders"]["url"], "pedidos.html")
        self.assertEqual(
            [link["url"] for link in data["orders"]["links"]],
            ["importar.html", "exportar_pedidos.html", "estado_pedidos.html"],
        )
        self.assertEqual(
            [link["url"] for link in data["print"]["links"]], ["documentos.html", "cola_impresion.html", "impresoras.html"]
        )
        # Mi perfil está en la misma página que los datos del comercio (2026-10-21).
        self.assertEqual(data["business"]["url"], "organizacion.html")
        self.assertEqual(
            [link["url"] for link in data["business"]["links"]],
            ["equipo.html", "equipo.html#perfiles", "equipo.html#actividad"],
        )

    def test_employee_sees_only_what_their_accesses_allow(self):
        deposito, _ = add_employee(self.owner, "dep@example.com", profile="Depósito")
        data = sections(self.client, deposito)
        self.assertEqual(list(data), ["orders", "print", "business", "help"])
        self.assertEqual([link["url"] for link in data["orders"]["links"]], ["estado_pedidos.html"])
        self.assertEqual([link["url"] for link in data["print"]["links"]], ["documentos.html", "cola_impresion.html"])
        self.assertEqual(
            [link["url"] for link in data["business"]["links"]], ["organizacion.html#perfil", "organizacion.html#comercio", "mi_actividad.html"]
        )

    def test_carriers_only_opens_stores_section_on_rates(self):
        employee, _ = add_employee(self.owner, "c@example.com", accesses=["carriers"])
        data = sections(self.client, employee)
        self.assertEqual(data["stores"]["url"], "tarifas_envio.html")

    def test_admin_also_gets_administration(self):
        admin = make_user("admin@example.com", group="admin")
        admin.is_staff = True
        admin.save()
        data = sections(self.client, admin)
        self.assertEqual(data["admin"]["url"], "gestionuser.html")
        self.assertEqual(list(data)[-1], "admin")

    def test_old_menu_is_still_there(self):
        self.assertIn("menu", self.client.get(URL, **bearer(self.owner)).data)


class OnboardingTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")

    def steps(self, user=None):
        onboarding = self.client.get(URL, **bearer(user or self.owner)).data["onboarding"]
        return onboarding if onboarding is None else {step["key"]: step["done"] for step in onboarding["steps"]}

    def test_new_business_has_four_steps_pending(self):
        self.assertEqual(self.steps(), {"store": False, "account": False, "template": False, "carrier": False})

    def test_steps_complete_as_the_business_sets_up(self):
        StoreConnection.objects.create(owner=self.owner, platform="tiendanube", external_store_id="1", status="active")
        AccountSettings.objects.create(user=self.owner, sender_name="Mi tienda")
        LabelTemplate.objects.create(owner=self.owner, name="Mía", width_cm=10, height_cm=15, design={})
        CarrierAccount.objects.create(owner=self.owner, carrier="oca", is_active=True)
        self.assertEqual(self.steps(), {"store": True, "account": True, "template": True, "carrier": True})

    def test_employees_do_not_get_the_setup_guide(self):
        employee, _ = add_employee(self.owner, "e@example.com", profile="Todo")
        self.assertIsNone(self.steps(employee))

    def test_admin_does_not_get_it_either(self):
        admin = make_user("admin@example.com", group="admin")
        self.assertIsNone(self.steps(admin))
