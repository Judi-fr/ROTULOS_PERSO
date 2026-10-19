"""Tiendas por empleado: uno limitado ve solo los pedidos de sus tiendas y los
cargados a mano; nada de otra tienda, ni pidiéndolo por id."""

from rest_framework.test import APITestCase

from apps.documents.models import Document
from apps.integrations.models import ShippingRate, StoreConnection
from apps.labels.models import LabelTemplate
from apps.orders.models import Address, Order

from .test_team_permissions import add_employee, bearer, make_user

DESIGN = {"destinatario": {"left": 6, "top": 38}}


def store(owner, tag):
    return StoreConnection.objects.create(
        owner=owner, platform=StoreConnection.Platform.TIENDANUBE, external_store_id=f"s-{tag}", name=f"Tienda {tag}"
    )


class StoreScopeTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.t1 = store(self.owner, "1")
        self.t2 = store(self.owner, "2")
        address = Address.objects.create(user=self.owner, recipient_name="Ana", street="Calle", number="1", city="CABA")
        self.o1 = Order.objects.create(user=self.owner, address=address, store_connection=self.t1, external_id="1")
        self.o2 = Order.objects.create(user=self.owner, address=address, store_connection=self.t2, external_id="2")
        self.manual = Order.objects.create(user=self.owner, address=address)
        LabelTemplate.objects.create(owner=self.owner, name="P", width_cm=10, height_cm=15, design=DESIGN, is_public=True)
        self.limited, member = add_employee(self.owner, "limitado@example.com", profile="Todo")
        member.all_stores = False
        member.save()
        member.stores.set([self.t1])
        self.everything, _ = add_employee(self.owner, "todas@example.com", profile="Todo")

    def ids(self, url, user=None):
        response = self.client.get(url, **bearer(user or self.limited))
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        data = response.data
        return {item["id"] for item in (data.get("results", data) if isinstance(data, dict) else data)}

    def test_limited_employee_sees_only_its_stores_and_manual_orders(self):
        self.assertEqual(self.ids("/api/v1/orders/"), {self.o1.id, self.manual.id})
        self.assertEqual(self.ids("/api/v1/integrations/stores/"), {self.t1.id})
        self.assertEqual(self.ids("/api/v1/orders/", self.everything), {self.o1.id, self.o2.id, self.manual.id})

    def test_limited_employee_gets_404_on_other_store_order(self):
        self.assertEqual(self.client.get(f"/api/v1/orders/{self.o2.id}/", **bearer(self.limited)).status_code, 404)
        self.assertEqual(self.client.get(f"/api/v1/orders/{self.o1.id}/", **bearer(self.limited)).status_code, 200)

    def test_batch_with_foreign_store_order_is_refused(self):
        response = self.client.post(
            "/api/v1/labels/batch/", {"order_ids": [self.o1.id, self.o2.id]}, format="json", **bearer(self.limited)
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("tiendas que no tenés habilitadas", str(response.data))
        self.assertFalse(Document.objects.exists())

    def test_batch_document_records_its_stores(self):
        response = self.client.post(
            "/api/v1/labels/batch/", {"order_ids": [self.o1.id, self.manual.id]}, format="json", **bearer(self.limited)
        )
        self.assertIn(response.status_code, (200, 201, 202), response.data)
        self.assertEqual(Document.objects.get().store_ids, [self.t1.id])

    def test_documents_with_foreign_orders_are_hidden(self):
        own = Document.objects.create(user=self.owner, name="T1", status=Document.Status.READY, store_ids=[self.t1.id])
        foreign = Document.objects.create(user=self.owner, name="T2", status=Document.Status.READY, store_ids=[self.t2.id, self.t1.id])
        manual = Document.objects.create(user=self.owner, name="Manual", status=Document.Status.READY, store_ids=[])
        self.assertEqual(self.ids("/api/v1/documents/"), {own.id, manual.id})
        response = self.client.get(f"/api/v1/documents/{foreign.id}/download/", **bearer(self.limited))
        self.assertEqual(response.status_code, 404)

    def test_new_store_is_hidden_until_enabled(self):
        t3 = store(self.owner, "3")
        self.assertNotIn(t3.id, self.ids("/api/v1/integrations/stores/"))
        self.assertIn(t3.id, self.ids("/api/v1/integrations/stores/", self.everything))

    def test_rates_and_settings_of_other_store_are_hidden(self):
        ShippingRate.objects.create(
            connection=self.t2, option_code="std", option_name="Estándar", postal_code_from="1000", postal_code_to="1999", price=100
        )
        self.assertEqual(self.ids(f"/api/v1/integrations/shipping-rates/?store={self.t2.id}"), set())
        response = self.client.patch(
            f"/api/v1/integrations/stores/{self.t2.id}/settings/", {"sender_name": "X"}, format="json", **bearer(self.limited)
        )
        self.assertEqual(response.status_code, 404)

    def test_saved_labels_follow_the_order_store(self):
        from apps.labels.models import Label

        template = LabelTemplate.objects.get(name="P", owner=self.owner)
        size = {"width_cm": 10, "height_cm": 15}
        own = Label.objects.create(user=self.owner, template=template, name="T1", order=self.o1, **size)
        Label.objects.create(user=self.owner, template=template, name="T2", order=self.o2, **size)
        loose = Label.objects.create(user=self.owner, template=template, name="Suelto", **size)
        self.assertEqual(self.ids("/api/v1/labels/labels/"), {own.id, loose.id})
        self.assertEqual(len(self.ids("/api/v1/labels/labels/", self.everything)), 3)
