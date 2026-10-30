"""El "Hoy" del panel (GET /orders/summary/) y la marca de rótulo impreso
(``Order.labels_printed_at``) que separa "para imprimir" de "para despachar"."""

from rest_framework.test import APITestCase

from apps.accounts import teams
from apps.accounts.tests.test_team_permissions import add_employee, bearer, make_owner
from apps.carriers.models import CarrierAccount, CarrierShipment
from apps.integrations.models import StoreConnection
from apps.integrations.tests.helpers import make_user
from apps.labels.models import LabelTemplate
from apps.orders.models import Address, Order
from apps.orders.print_marks import mark_labels_printed

URL = "/api/v1/orders/summary/"
DESIGN = {"destinatario": {"left": 6, "top": 38}}


class OrderSummaryTests(APITestCase):
    def setUp(self):
        self.owner = make_owner("dueno@example.com")
        self.biz = teams.data_owner(self.owner)
        self.store_a = StoreConnection.objects.create(
            owner=self.biz, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="a", name="A"
        )
        self.store_b = StoreConnection.objects.create(
            owner=self.biz, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="b", name="B"
        )
        self.address = Address.objects.create(user=self.biz, recipient_name="Ana", street="Calle", number="1", city="CABA")

    def order(self, status=Order.Status.CREATED, store=None, printed=False, n=[0]):
        n[0] += 1
        order = Order.objects.create(
            user=self.biz, address=self.address, store_connection=store, external_id=f"x{n[0]}"
        )
        if status != Order.Status.CREATED:
            Order.objects.filter(pk=order.pk).update(status=status)
        if printed:
            mark_labels_printed([order.pk])
        return order

    def summary(self, user=None):
        response = self.client.get(URL, **bearer(user or self.owner))
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return response.data

    def test_separa_para_imprimir_para_despachar_en_camino_y_problemas(self):
        self.order()
        self.order(store=self.store_a)
        self.order(status=Order.Status.PREPARING)
        self.order(printed=True)
        self.order(status=Order.Status.PREPARING, printed=True)
        self.order(status=Order.Status.DISPATCHED, printed=True)
        self.order(status=Order.Status.IN_TRANSIT)
        self.order(status=Order.Status.DELIVERED)
        self.order(status=Order.Status.CANCELLED)
        troubled = self.order(status=Order.Status.IN_TRANSIT, printed=True)
        account = CarrierAccount.objects.create(owner=self.biz, carrier="andreani")
        for number, status in enumerate((CarrierShipment.Status.ISSUE, CarrierShipment.Status.RETURNING)):
            CarrierShipment.objects.create(
                order=troubled, account=account, carrier="andreani", status=status, tracking_number=f"36000{number}"
            )

        self.assertEqual(
            self.summary(),
            {"to_print": 3, "to_dispatch": 2, "on_the_way": 3, "shipment_problems": 1},
        )

    def test_otro_comercio_no_cuenta(self):
        self.order()
        other = make_owner("otro@example.com")
        other_biz = teams.data_owner(other)
        address = Address.objects.create(user=other_biz, recipient_name="Bea", street="Calle", number="2", city="CABA")
        Order.objects.create(user=other_biz, address=address)
        self.assertEqual(self.summary()["to_print"], 1)
        self.assertEqual(self.summary(other)["to_print"], 1)

    def test_empleado_limitado_cuenta_sus_tiendas_y_los_cargados_a_mano(self):
        self.order(store=self.store_a)
        self.order(store=self.store_b)
        self.order()
        limited, member = add_employee(self.owner, "limitado@example.com", profile="Todo")
        member.all_stores = False
        member.save()
        member.stores.set([self.store_a])
        self.assertEqual(self.summary(limited)["to_print"], 2)
        self.assertEqual(self.summary()["to_print"], 3)

    def test_sin_comercio_no_hay_resumen(self):
        nobody = make_user("suelto@example.com")
        self.assertEqual(self.client.get(URL, **bearer(nobody)).status_code, 403)

    def test_imprimir_en_lote_marca_los_pedidos_una_sola_vez(self):
        LabelTemplate.objects.create(owner=self.biz, name="P", width_cm=10, height_cm=15, design=DESIGN, is_public=True)
        first, second = self.order(), self.order()
        response = self.client.post(
            "/api/v1/labels/batch/", {"order_ids": [first.pk]}, format="json", **bearer(self.owner)
        )
        self.assertIn(response.status_code, (201, 202), getattr(response, "data", None))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNotNone(first.labels_printed_at)
        self.assertIsNone(second.labels_printed_at)
        self.assertEqual(self.summary(), {"to_print": 1, "to_dispatch": 1, "on_the_way": 0, "shipment_problems": 0})

        printed_at = first.labels_printed_at
        self.client.post("/api/v1/labels/batch/", {"order_ids": [first.pk]}, format="json", **bearer(self.owner))
        first.refresh_from_db()
        self.assertEqual(first.labels_printed_at, printed_at)
