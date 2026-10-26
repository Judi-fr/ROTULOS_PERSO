"""Cada área exige su propio acceso: "crear pedidos" ya no habilita a la vez
tiendas, transportistas, tarifas y despacho."""

from rest_framework.test import APITestCase

from apps.accounts import teams
from apps.accounts.tests.test_team_permissions import add_employee, bearer, make_user
from apps.accounts.tests.team_helpers import new_business

ALL = set(teams.ACCESSES)

# (método, url, cuerpo, acceso que exige)
GATES = [
    ("post", "/api/v1/integrations/tiendanube/install-share-link/", {}, "stores"),
    ("patch", "/api/v1/integrations/stores/999999/settings/", {}, "stores"),
    ("post", "/api/v1/integrations/stores/999999/disconnect/", {}, "stores"),
    ("get", "/api/v1/integrations/shipping-rates/", None, "carriers"),
    ("put", "/api/v1/carriers/accounts/andreani/", {}, "carriers"),
    ("get", "/api/v1/carriers/checkout/", None, "carriers"),
    ("post", "/api/v1/carriers/shipments/", {}, "orders.dispatch"),
    ("post", "/api/v1/orders/999999/ship/", {}, "orders.dispatch"),
    ("post", "/api/v1/orders/bulk-status/", {"order_ids": [999999], "status": "dispatched"}, "orders.dispatch"),
    ("post", "/api/v1/orders/manifest/", {"order_ids": [999999]}, "orders.dispatch"),
    ("get", "/api/v1/orders/export/?file_type=csv", None, "orders.export"),
    ("post", "/api/v1/printing/printers/", {}, "printers"),
    ("get", "/api/v1/printing/agents/", None, "printers"),
    ("patch", "/api/v1/auth/me/account/", {}, "account"),
]


class TeamGateTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        new_business(self.owner)

    def call(self, user, method, url, body):
        return getattr(self.client, method)(url, body, format="json", **bearer(user))

    def test_each_area_requires_its_own_access(self):
        for index, (method, url, body, access) in enumerate(GATES):
            with self.subTest(url=url, access=access):
                without, _ = add_employee(self.owner, f"sin{index}@example.com", accesses=sorted(ALL - {access}))
                self.assertEqual(self.call(without, method, url, body).status_code, 403)
                with_it, _ = add_employee(self.owner, f"con{index}@example.com", accesses=sorted(ALL))
                self.assertNotEqual(self.call(with_it, method, url, body).status_code, 403)

    def test_seeing_orders_is_enough_to_list_stores_and_shipments(self):
        # Mis pedidos arma el filtro por tienda y muestra el estado de los envíos.
        viewer, _ = add_employee(self.owner, "ver@example.com", accesses=["orders.view"])
        for url in ("/api/v1/integrations/stores/", "/api/v1/carriers/shipments/", "/api/v1/carriers/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url, **bearer(viewer)).status_code, 200)
