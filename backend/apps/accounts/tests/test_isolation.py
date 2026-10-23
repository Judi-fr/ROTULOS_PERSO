"""Los datos son del comercio (cuelgan de su usuario de datos): un empleado
trabaja sobre ellos, y nada cruza de un comercio a otro."""

from urllib.parse import parse_qs, urlparse

from django.test import override_settings
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.carriers.models import CarrierAccount
from apps.documents.models import Document
from apps.integrations.models import ShippingRate, StoreConnection
from apps.integrations.stores import read_oauth_state
from apps.labels.models import LabelTemplate
from apps.orders.models import Address, Order
from apps.printing.models import Printer

from apps.accounts import teams

from .test_team_permissions import add_employee, bearer, make_owner

DESIGN = {"destinatario": {"left": 6, "top": 38}}


def seed_team_data(owner, tag):
    store = StoreConnection.objects.create(
        owner=owner, platform=StoreConnection.Platform.TIENDANUBE, external_store_id=f"store-{tag}", name=f"Tienda {tag}"
    )
    address = Address.objects.create(user=owner, street="Calle", number="1", city="CABA")
    order = Order.objects.create(user=owner, address=address, store_connection=store, external_id=f"o-{tag}")
    template = LabelTemplate.objects.create(owner=owner, name=f"Plantilla {tag}", width_cm=10, height_cm=15, design=DESIGN)
    document = Document.objects.create(user=owner, name=f"Lote {tag}", status=Document.Status.READY)
    printer = Printer.objects.create(owner=owner, name=f"Zebra {tag}", format="pdf")
    account = CarrierAccount.objects.create(owner=owner, carrier="andreani", client_code=f"cli-{tag}")
    rate = ShippingRate.objects.create(
        connection=store, option_code="std", option_name="Estándar", postal_code_from="1000", postal_code_to="1999", price=100
    )
    return {
        "store": store, "address": address, "order": order, "template": template,
        "document": document, "printer": printer, "account": account, "rate": rate,
    }


class IsolationTests(APITestCase):
    def setUp(self):
        self.owner_a = make_owner("owner-a@example.com")
        self.employee, _ = add_employee(self.owner_a, "emp-a@example.com", profile="Todo")
        self.owner_b = make_owner("owner-b@example.com")
        self.data_a = teams.data_owner(self.owner_a)
        self.a = seed_team_data(self.data_a, "a")
        self.b = seed_team_data(teams.data_owner(self.owner_b), "b")

    def get(self, url, user=None):
        return self.client.get(url, **bearer(user or self.employee))

    def ids(self, url):
        response = self.get(url)
        self.assertEqual(response.status_code, 200, (url, getattr(response, "data", None)))
        data = response.data
        items = data.get("results", data) if isinstance(data, dict) else data
        return {item["id"] for item in items}

    def test_employee_sees_owner_orders_stores_templates_and_documents(self):
        self.assertEqual(self.ids("/api/v1/orders/"), {self.a["order"].id})
        self.assertEqual(self.ids("/api/v1/integrations/stores/"), {self.a["store"].id})
        self.assertIn(self.a["template"].id, self.ids("/api/v1/labels/templates/"))
        self.assertEqual(self.ids("/api/v1/documents/"), {self.a["document"].id})
        self.assertEqual(self.ids("/api/v1/printing/printers/"), {self.a["printer"].id})
        self.assertEqual(self.ids("/api/v1/integrations/shipping-rates/"), {self.a["rate"].id})
        carriers = {row["code"]: row["account"] for row in self.get("/api/v1/carriers/").data["results"]}
        self.assertEqual(carriers["andreani"]["client_code"], "cli-a")

    def test_what_an_employee_creates_belongs_to_the_team(self):
        order = self.client.post(
            "/api/v1/orders/manual/",
            {"destinatario": "Ana", "domicilio": "Calle", "numero": "2", "ciudad": "CABA"},
            format="json",
            **bearer(self.employee),
        )
        self.assertEqual(order.status_code, 201, order.data)
        self.assertEqual(Order.objects.get(id=order.data["id"]).user, self.data_a)

        template = self.client.post(
            "/api/v1/labels/templates/",
            {"name": "Del empleado", "width_cm": 10, "height_cm": 15, "design": DESIGN},
            format="json",
            **bearer(self.employee),
        )
        self.assertEqual(template.status_code, 201, template.data)
        self.assertEqual(LabelTemplate.objects.get(id=template.data["id"]).owner, self.data_a)

        address = self.client.post(
            "/api/v1/addresses/",
            {"recipient_name": "Ana", "street": "Calle", "number": "3", "city": "CABA"},
            format="json",
            **bearer(self.employee),
        )
        self.assertEqual(address.status_code, 201, address.data)
        self.assertEqual(Address.objects.get(id=address.data["id"]).user, self.data_a)

        # La auditoría nombra a la persona real.
        self.assertTrue(AuditLog.objects.filter(actor=self.employee).exists())
        self.assertFalse(AuditLog.objects.filter(actor=self.owner_a).exists())

    def test_nothing_crosses_teams(self):
        b = self.b
        for url in (
            f"/api/v1/orders/{b['order'].id}/",
            f"/api/v1/integrations/stores/{b['store'].id}/",
            f"/api/v1/documents/{b['document'].id}/download/",
            f"/api/v1/labels/templates/{b['template'].id}/",
            f"/api/v1/printing/printers/{b['printer'].id}/",
            f"/api/v1/integrations/shipping-rates/{b['rate'].id}/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.get(url).status_code, 404)
        # Y al revés: el dueño de B no ve nada de A.
        self.assertEqual(self.ids_for(self.owner_b, "/api/v1/orders/"), {b["order"].id})

    def ids_for(self, user, url):
        data = self.get(url, user).data
        return {item["id"] for item in data.get("results", data)}

    @override_settings(TIENDANUBE_APP_ID="9999", TIENDANUBE_CLIENT_SECRET="secreto")
    def test_store_connect_from_employee_is_owned_by_the_team(self):
        response = self.get("/api/v1/integrations/tiendanube/install-url/")
        self.assertEqual(response.status_code, 200, response.data)
        state = parse_qs(urlparse(response.data["authorize_url"]).query)["state"][0]
        self.assertEqual(read_oauth_state(state, "tiendanube"), self.data_a)
