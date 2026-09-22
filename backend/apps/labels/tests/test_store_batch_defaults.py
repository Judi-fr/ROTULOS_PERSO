"""Lote de rótulos de pedidos de una tienda: sin ``template_id`` se usa la
plantilla preferida de esa tienda, y el logo de la tienda entra al PDF."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from apps.integrations.models import StoreConnection
from apps.labels.batch_views import LabelBatchView, _render_params_for_item
from apps.labels.models import LabelTemplate
from apps.orders.models import Address, Order

User = get_user_model()

VALID_DESIGN = {
    "logo": {"left": 5, "top": 5},
    "destinatario": {"left": 10, "top": 40, "text": "{{destinatario}}"},
}


class StoreBatchDefaultsTests(APITestCase):
    def setUp(self):
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(
            username="vendedor5@example.com", email="vendedor5@example.com", password="Clave123!"
        )
        self.user.groups.add(subscriber)

        # La plantilla pública "por defecto" del lote es la más antigua
        # activa: la que siembran las migraciones de labels.
        self.system_template = (
            LabelTemplate.objects.filter(is_public=True, is_active=True).order_by("id").first()
        )
        self.store_template = LabelTemplate.objects.create(
            name="La de mi tienda", owner=self.user,
            width_cm=10, height_cm=15, design=VALID_DESIGN,
        )
        self.store = StoreConnection.objects.create(
            owner=self.user,
            platform=StoreConnection.Platform.TIENDANUBE,
            external_store_id="555",
            name="Tienda E",
            default_template=self.store_template,
        )
        self.address = Address.objects.create(
            user=self.user, origin=Address.Origin.SHIPMENT,
            recipient_name="María Gómez", street="Corrientes", number="1000", city="CABA",
        )
        self.order = Order.objects.create(
            user=self.user, address=self.address, store_connection=self.store, external_id="s1"
        )

    def test_sin_template_id_usa_la_plantilla_preferida_de_la_tienda(self):
        template = LabelBatchView()._resolve_template(self.user, None, [self.order])

        self.assertEqual(template, self.store_template)

    def test_con_template_id_manda_el_que_pidio_el_usuario(self):
        template = LabelBatchView()._resolve_template(
            self.user, self.system_template.pk, [self.order]
        )

        self.assertEqual(template, self.system_template)

    def test_pedidos_de_tiendas_distintas_caen_a_la_plantilla_publica(self):
        other_store = StoreConnection.objects.create(
            owner=self.user,
            platform=StoreConnection.Platform.TIENDANUBE,
            external_store_id="666",
            name="Tienda F",
        )
        other_order = Order.objects.create(
            user=self.user, address=self.address, store_connection=other_store, external_id="s2"
        )

        template = LabelBatchView()._resolve_template(self.user, None, [self.order, other_order])

        self.assertEqual(template, self.system_template)

    def test_un_pedido_sin_tienda_cae_a_la_plantilla_publica(self):
        own_order = Order.objects.create(user=self.user, address=self.address)

        template = LabelBatchView()._resolve_template(self.user, None, [own_order])

        self.assertEqual(template, self.system_template)

    def test_el_logo_del_rotulo_sale_de_la_tienda_del_pedido(self):
        # Sin logo cargado, el pedido se dibuja sin logo (como antes).
        self.assertIsNone(_render_params_for_item(self.order, self.store_template)[4])

        self.store.logo = "store_logos/2026/09/logo.png"
        self.store.save(update_fields=["logo"])
        self.order.refresh_from_db()

        logo_file = _render_params_for_item(self.order, self.store_template)[4]
        self.assertTrue(logo_file)
        self.assertIn("logo.png", logo_file.name)
