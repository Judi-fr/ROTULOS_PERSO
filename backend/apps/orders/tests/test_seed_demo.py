"""``manage.py seed_demo``: datos de ejemplo, idempotentes y nunca en producción."""

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from apps.integrations.models import ShippingRate, StoreConnection
from apps.orders.management.commands.seed_demo import DEMO_EMAIL, MANUAL_ORDERS, STORE_ORDERS
from apps.orders.models import Address, Order


@override_settings(DEBUG=True)
class SeedDemoTests(TestCase):
    def _run(self, **options):
        call_command("seed_demo", stdout=StringIO(), **options)

    def test_crea_cuenta_tienda_tarifas_y_pedidos_en_todos_los_estados(self):
        self._run()

        user = get_user_model().objects.get(username=DEMO_EMAIL)
        self.assertTrue(user.groups.filter(name="operator").exists())
        store = StoreConnection.objects.get(owner=user)
        self.assertEqual(store.platform, StoreConnection.Platform.EMPRETIENDA)
        self.assertTrue(ShippingRate.objects.filter(connection=store).exists())

        store_orders = Order.objects.filter(store_connection=store)
        self.assertEqual(store_orders.count(), len(STORE_ORDERS))
        self.assertEqual(set(store_orders.values_list("status", flat=True)), set(Order.Status.values))
        self.assertEqual(
            Order.objects.filter(user=user, store_connection=None).count(), len(MANUAL_ORDERS)
        )
        self.assertTrue(Address.objects.filter(user=user, origin=Address.Origin.OWN).exists())

    def test_correrlo_dos_veces_no_duplica(self):
        self._run()
        counts = (Order.objects.count(), Address.objects.count(), ShippingRate.objects.count())
        self._run()
        self.assertEqual(
            (Order.objects.count(), Address.objects.count(), ShippingRate.objects.count()), counts
        )

    @override_settings(DEBUG=False)
    def test_sin_debug_se_niega_salvo_force(self):
        with self.assertRaises(CommandError):
            self._run()
        self.assertFalse(Order.objects.exists())

        self._run(force=True)
        self.assertTrue(Order.objects.exists())
