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


def counts(summary):
    """Los números del resumen, sin el desglose por tienda ni la semana."""
    return {key: value for key, value in summary.items() if key not in ("by_store", "week")}


class SummaryFixtures(APITestCase):
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



class OrderSummaryTests(SummaryFixtures):
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
            counts(self.summary()),
            {"to_print": 3, "to_dispatch": 2, "on_the_way": 3, "shipment_problems": 1, "delayed": 0, "delayed_days": 2},
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
        self.assertEqual(counts(self.summary()), {"to_print": 1, "to_dispatch": 1, "on_the_way": 0, "shipment_problems": 0, "delayed": 0, "delayed_days": 2})

        printed_at = first.labels_printed_at
        self.client.post("/api/v1/labels/batch/", {"order_ids": [first.pk]}, format="json", **bearer(self.owner))
        first.refresh_from_db()
        self.assertEqual(first.labels_printed_at, printed_at)


class DelayedAndPrintedFilterTests(SummaryFixtures):
    """Pedidos demorados en el resumen, los filtros que abren los números del
    panel (``?labels_printed=``, ``?older_than_days=``) y la marca en la lista."""

    def age(self, order, days):
        from django.utils import timezone

        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timezone.timedelta(days=days, minutes=1))
        return order

    def listed(self, query):
        response = self.client.get(f"/api/v1/orders/?{query}", **bearer(self.owner))
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        return sorted(row["id"] for row in response.data["results"])

    def test_cuenta_los_pendientes_que_esperan_hace_mas_de_dos_dias(self):
        self.age(self.order(), 3)
        self.age(self.order(status=Order.Status.PREPARING, printed=True), 2)
        self.age(self.order(), 1)
        self.age(self.order(status=Order.Status.DISPATCHED), 5)
        self.age(self.order(status=Order.Status.CANCELLED), 5)
        data = self.summary()
        self.assertEqual(data["delayed"], 2)
        self.assertEqual(data["delayed_days"], 2)

    def test_filtra_por_rotulo_impreso(self):
        printed = self.order(printed=True)
        pending = self.order()
        self.assertEqual(self.listed("labels_printed=1"), [printed.pk])
        self.assertEqual(self.listed("labels_printed=0"), [pending.pk])

    def test_labels_printed_invalido_es_400(self):
        response = self.client.get("/api/v1/orders/?labels_printed=quizas", **bearer(self.owner))
        self.assertEqual(response.status_code, 400)

    def test_filtra_los_que_esperan_mas_de_n_dias(self):
        old = self.age(self.order(), 3)
        self.age(self.order(), 1)
        self.assertEqual(self.listed("older_than_days=2"), [old.pk])

    def test_older_than_days_invalido_es_400(self):
        response = self.client.get("/api/v1/orders/?older_than_days=-1", **bearer(self.owner))
        self.assertEqual(response.status_code, 400)

    def test_la_lista_trae_cuando_se_imprimio_el_rotulo(self):
        self.order(printed=True)
        response = self.client.get("/api/v1/orders/", **bearer(self.owner))
        self.assertIsNotNone(response.data["results"][0]["labels_printed_at"])

    def test_omitir_existentes_saltea_los_ya_impresos_en_lote(self):
        LabelTemplate.objects.create(owner=self.biz, name="P", width_cm=10, height_cm=15, design=DESIGN, is_public=True)
        printed, fresh = self.order(printed=True), self.order()
        response = self.client.post(
            "/api/v1/labels/batch/",
            {"order_ids": [printed.pk, fresh.pk], "skip_existing": True},
            format="json",
            **bearer(self.owner),
        )
        self.assertIn(response.status_code, (201, 202), getattr(response, "data", None))
        self.assertEqual(response.data["item_count"], 1)
        self.assertIn("1 pedido(s) omitido(s)", response.data["error_message"])

    def test_sin_omitir_se_reimprime(self):
        LabelTemplate.objects.create(owner=self.biz, name="P", width_cm=10, height_cm=15, design=DESIGN, is_public=True)
        printed = self.order(printed=True)
        response = self.client.post(
            "/api/v1/labels/batch/", {"order_ids": [printed.pk]}, format="json", **bearer(self.owner)
        )
        self.assertIn(response.status_code, (201, 202), getattr(response, "data", None))

    def test_omitir_existentes_con_todos_ya_impresos_avisa(self):
        LabelTemplate.objects.create(owner=self.biz, name="P", width_cm=10, height_cm=15, design=DESIGN, is_public=True)
        printed = self.order(printed=True)
        response = self.client.post(
            "/api/v1/labels/batch/",
            {"order_ids": [printed.pk], "skip_existing": True},
            format="json",
            **bearer(self.owner),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("ya tienen", response.data["detail"])


class StoreBreakdownAndWeekTests(SummaryFixtures):
    """El desglose por tienda de "para imprimir"/"para despachar" y la serie de
    los últimos 7 días (pedidos que entraron y que salieron) del panel."""

    def shipped_event(self, order, status, days_ago):
        from django.utils import timezone

        from apps.orders.models import OrderStatusEvent

        event = OrderStatusEvent.objects.create(order=order, status=status)
        OrderStatusEvent.objects.filter(pk=event.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=days_ago)
        )

    def created(self, order, days_ago):
        from django.utils import timezone

        Order.objects.filter(pk=order.pk).update(created_at=timezone.now() - timezone.timedelta(days=days_ago))
        return order

    def test_desglosa_por_tienda_con_los_cargados_a_mano_al_final(self):
        self.order(store=self.store_b)
        self.order(store=self.store_a)
        self.order(store=self.store_a, printed=True)
        self.order()
        self.order(status=Order.Status.DISPATCHED, store=self.store_b)
        rows = self.summary()["by_store"]
        self.assertEqual(
            rows,
            [
                {"store": self.store_a.pk, "name": "A", "to_print": 1, "to_dispatch": 1},
                {"store": self.store_b.pk, "name": "B", "to_print": 1, "to_dispatch": 0},
                {"store": "manual", "name": "Cargados a mano", "to_print": 1, "to_dispatch": 0},
            ],
        )

    def test_una_tienda_sin_pendientes_no_aparece(self):
        self.order(store=self.store_a)
        self.order(status=Order.Status.DELIVERED, store=self.store_b)
        self.assertEqual([row["store"] for row in self.summary()["by_store"]], [self.store_a.pk])

    def test_el_empleado_limitado_ve_solo_sus_tiendas_en_el_desglose(self):
        self.order(store=self.store_a)
        self.order(store=self.store_b)
        self.order()
        limited, member = add_employee(self.owner, "limitado2@example.com", profile="Todo")
        member.all_stores = False
        member.save()
        member.stores.set([self.store_a])
        self.assertEqual([row["store"] for row in self.summary(limited)["by_store"]], [self.store_a.pk, "manual"])

    def test_la_semana_son_siete_dias_terminando_hoy(self):
        from django.utils import timezone

        week = self.summary()["week"]
        self.assertEqual(len(week), 7)
        self.assertEqual(week[-1]["date"], timezone.localdate().isoformat())
        self.assertEqual(week[0]["date"], (timezone.localdate() - timezone.timedelta(days=6)).isoformat())
        self.assertTrue(all(day["entered"] == 0 and day["dispatched"] == 0 for day in week))

    def test_cuenta_los_que_entraron_por_dia(self):
        self.order()
        self.order(status=Order.Status.CANCELLED)
        self.created(self.order(), 2)
        self.created(self.order(), 9)
        week = self.summary()["week"]
        self.assertEqual(week[-1]["entered"], 2)
        self.assertEqual(week[-3]["entered"], 1)
        self.assertEqual(sum(day["entered"] for day in week), 3)

    def test_cuenta_cada_pedido_despachado_una_vez_el_dia_que_salio(self):
        both = self.order(status=Order.Status.IN_TRANSIT)
        self.shipped_event(both, Order.Status.DISPATCHED, 1)
        self.shipped_event(both, Order.Status.IN_TRANSIT, 0)
        straight = self.order(status=Order.Status.DELIVERED)
        self.shipped_event(straight, Order.Status.DELIVERED, 0)
        old = self.order(status=Order.Status.DELIVERED)
        self.shipped_event(old, Order.Status.DISPATCHED, 10)
        self.shipped_event(old, Order.Status.DELIVERED, 0)
        week = self.summary()["week"]
        self.assertEqual(week[-2]["dispatched"], 1)
        self.assertEqual(week[-1]["dispatched"], 1)
        self.assertEqual(sum(day["dispatched"] for day in week), 2)

    def test_la_semana_no_mezcla_comercios(self):
        other = make_owner("otro2@example.com")
        other_biz = teams.data_owner(other)
        address = Address.objects.create(user=other_biz, recipient_name="Bea", street="Calle", number="2", city="CABA")
        Order.objects.create(user=other_biz, address=address)
        self.assertEqual(sum(day["entered"] for day in self.summary()["week"]), 0)
