"""Estadísticas del comercio (GET /orders/stats/, pestaña Estadísticas de Mi
comercio): tiempo de despacho, transportistas y dónde están los compradores.
Cada bloque tiene su acceso de equipo, que el dueño reparte en los perfiles."""

from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts import teams
from apps.accounts.tests.test_team_permissions import add_employee, bearer, make_owner
from apps.carriers.models import CarrierAccount, CarrierShipment
from apps.integrations.models import StoreConnection
from apps.orders.models import Address, Order, OrderStatusEvent

URL = "/api/v1/orders/stats/"
STATS_ACCESSES = ("stats.dispatch", "stats.carriers", "stats.buyers")


def ago(**delta):
    return timezone.now() - timezone.timedelta(**delta)


class StatsFixtures(APITestCase):
    def setUp(self):
        self.owner = make_owner("dueno@example.com")
        self.biz = teams.data_owner(self.owner)
        self.store_a = StoreConnection.objects.create(
            owner=self.biz, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="a", name="A"
        )
        self.store_b = StoreConnection.objects.create(
            owner=self.biz, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="b", name="B"
        )
        self.account = CarrierAccount.objects.create(owner=self.biz, carrier="andreani")
        self.n = 0

    def order(self, *, created, status=Order.Status.CREATED, store=None, city="CABA", state="Buenos Aires", carrier=""):
        self.n += 1
        address = Address.objects.create(user=self.biz, recipient_name="Ana", street="Calle", number="1", city=city, state=state)
        order = Order.objects.create(user=self.biz, address=address, store_connection=store, external_id=f"x{self.n}")
        Order.objects.filter(pk=order.pk).update(status=status, created_at=created, carrier=carrier)
        order.refresh_from_db()
        return order

    def event(self, order, status, at):
        event = OrderStatusEvent.objects.create(order=order, status=status)
        OrderStatusEvent.objects.filter(pk=event.pk).update(created_at=at)

    def shipment(self, order, status=CarrierShipment.Status.IN_TRANSIT, price=None):
        self.n += 1
        return CarrierShipment.objects.create(
            order=order, account=self.account, carrier="andreani", status=status,
            tracking_number=f"36{self.n}", quoted_price=price,
        )

    def stats(self, user=None, query="", expect=200):
        response = self.client.get(f"{URL}{query}", **bearer(user or self.owner))
        self.assertEqual(response.status_code, expect, getattr(response, "data", None))
        return response.data


class StatsAccessTests(StatsFixtures):
    def test_los_accesos_de_estadisticas_estan_en_el_catalogo(self):
        for access in STATS_ACCESSES:
            self.assertIn(access, teams.ACCESSES)

    def test_solo_el_perfil_todo_arranca_con_estadisticas(self):
        for access in STATS_ACCESSES:
            self.assertIn(access, teams.SUGGESTED_PROFILES["Todo"])
            self.assertNotIn(access, teams.SUGGESTED_PROFILES["Depósito"])
            self.assertNotIn(access, teams.SUGGESTED_PROFILES["Atención"])

    def test_el_dueno_ve_los_tres_bloques(self):
        data = self.stats()
        self.assertEqual(data["allowed"], ["dispatch", "carriers", "buyers"])
        self.assertIn("dispatch", data)
        self.assertIn("carriers", data)
        self.assertIn("buyers", data)

    def test_un_empleado_ve_solo_los_bloques_que_le_dieron(self):
        employee, _ = add_employee(self.owner, "emp@example.com", accesses=["orders.view", "stats.carriers"])
        data = self.stats(employee)
        self.assertEqual(data["allowed"], ["carriers"])
        self.assertNotIn("dispatch", data)
        self.assertNotIn("buyers", data)

    def test_sin_ningun_acceso_de_estadisticas_es_403(self):
        employee, _ = add_employee(self.owner, "dep@example.com", profile="Depósito")
        self.stats(employee, expect=403)

    def test_dias_invalidos_es_400(self):
        self.stats(query="?days=15", expect=400)


class DispatchStatsTests(StatsFixtures):
    def test_promedio_mediana_y_despachados_en_24_y_48_horas(self):
        for hours in (10, 20, 30, 60):
            order = self.order(created=ago(days=5), status=Order.Status.DISPATCHED)
            self.event(order, Order.Status.DISPATCHED, order.created_at + timezone.timedelta(hours=hours))
        self.order(created=ago(days=1))
        self.order(created=ago(days=1), status=Order.Status.CANCELLED)
        dispatch = self.stats()["dispatch"]
        self.assertEqual(dispatch["shipped"], 4)
        self.assertEqual(dispatch["waiting"], 1)
        self.assertEqual(dispatch["avg_hours"], 30.0)
        self.assertEqual(dispatch["median_hours"], 25.0)
        self.assertEqual(dispatch["within_24h"], 50)
        self.assertEqual(dispatch["within_48h"], 75)

    def test_cuenta_el_primer_estado_de_salida(self):
        order = self.order(created=ago(days=3), status=Order.Status.DELIVERED)
        self.event(order, Order.Status.DELIVERED, order.created_at + timezone.timedelta(hours=12))
        self.event(order, Order.Status.DISPATCHED, order.created_at + timezone.timedelta(hours=6))
        self.assertEqual(self.stats()["dispatch"]["avg_hours"], 6.0)

    def test_fuera_del_periodo_no_cuenta(self):
        order = self.order(created=ago(days=40), status=Order.Status.DISPATCHED)
        self.event(order, Order.Status.DISPATCHED, order.created_at + timezone.timedelta(hours=5))
        self.assertEqual(self.stats(query="?days=30")["dispatch"]["shipped"], 0)
        self.assertEqual(self.stats(query="?days=90")["dispatch"]["shipped"], 1)

    def test_sin_despachos_no_hay_promedio(self):
        dispatch = self.stats()["dispatch"]
        self.assertEqual(dispatch["shipped"], 0)
        self.assertIsNone(dispatch["avg_hours"])
        self.assertIsNone(dispatch["within_24h"])


class CarrierStatsTests(StatsFixtures):
    def test_por_transportista_entrega_problemas_y_costo(self):
        delivered = self.order(created=ago(days=6), status=Order.Status.DELIVERED, carrier="Andreani")
        self.event(delivered, Order.Status.DISPATCHED, ago(days=5))
        self.event(delivered, Order.Status.DELIVERED, ago(days=2))
        self.shipment(delivered, CarrierShipment.Status.DELIVERED, Decimal("1000"))
        troubled = self.order(created=ago(days=6), status=Order.Status.IN_TRANSIT, carrier="Andreani")
        self.event(troubled, Order.Status.DISPATCHED, ago(days=5))
        self.shipment(troubled, CarrierShipment.Status.RETURNING, Decimal("500"))
        manual = self.order(created=ago(days=4), status=Order.Status.DISPATCHED, carrier="Moto Juan")
        self.event(manual, Order.Status.DISPATCHED, ago(days=3))
        rows = {row["carrier"]: row for row in self.stats()["carriers"]}
        self.assertEqual(rows["Andreani"]["shipped"], 2)
        self.assertEqual(rows["Andreani"]["delivered"], 1)
        self.assertEqual(rows["Andreani"]["avg_delivery_days"], 3.0)
        self.assertEqual(rows["Andreani"]["problems"], 1)
        self.assertEqual(rows["Andreani"]["problem_rate"], 50)
        self.assertEqual(rows["Andreani"]["cost_total"], "1500.00")
        self.assertEqual(rows["Andreani"]["cost_avg"], "750.00")
        self.assertEqual(rows["Moto Juan"]["shipped"], 1)
        self.assertIsNone(rows["Moto Juan"]["avg_delivery_days"])
        self.assertIsNone(rows["Moto Juan"]["cost_total"])
        self.assertEqual(list(rows), ["Andreani", "Moto Juan"])

    def test_sin_transportista_queda_como_sin_indicar(self):
        order = self.order(created=ago(days=2), status=Order.Status.DISPATCHED)
        self.event(order, Order.Status.DISPATCHED, ago(days=1))
        self.assertEqual(self.stats()["carriers"][0]["carrier"], "Sin indicar")


class BuyerStatsTests(StatsFixtures):
    def test_provincias_y_ciudades_mas_frecuentes(self):
        for _ in range(3):
            self.order(created=ago(days=1), city="Rosario", state="Santa Fe")
        self.order(created=ago(days=1), city="caba ", state="Ciudad Autónoma de Buenos Aires")
        self.order(created=ago(days=1), city="CABA", state="Ciudad Autónoma de Buenos Aires")
        self.order(created=ago(days=1), city="Rosario", state="Santa Fe", status=Order.Status.CANCELLED)
        buyers = self.stats()["buyers"]
        self.assertEqual(buyers["total"], 5)
        self.assertEqual(buyers["states"][0], {"name": "Santa Fe", "count": 3})
        self.assertEqual(buyers["states"][1], {"name": "Ciudad Autónoma de Buenos Aires", "count": 2})
        self.assertEqual(buyers["cities"][0], {"name": "Rosario", "state": "Santa Fe", "count": 3})
        self.assertEqual(buyers["cities"][1]["count"], 2)


class StatsScopeTests(StatsFixtures):
    def test_otro_comercio_no_cuenta(self):
        other = make_owner("otro@example.com")
        other_biz = teams.data_owner(other)
        address = Address.objects.create(user=other_biz, recipient_name="Bea", street="C", number="2", city="X", state="Y")
        Order.objects.create(user=other_biz, address=address)
        self.assertEqual(self.stats()["buyers"]["total"], 0)

    def test_el_empleado_limitado_ve_solo_sus_tiendas_y_los_cargados_a_mano(self):
        self.order(created=ago(days=1), store=self.store_a)
        self.order(created=ago(days=1), store=self.store_b)
        self.order(created=ago(days=1))
        employee, member = add_employee(self.owner, "lim@example.com", accesses=["orders.view", "stats.buyers"])
        member.all_stores = False
        member.save()
        member.stores.set([self.store_a])
        self.assertEqual(self.stats(employee)["buyers"]["total"], 2)
