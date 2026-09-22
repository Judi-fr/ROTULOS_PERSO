"""Tests de ``build_label_context``: quién sale impreso como destinatario."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.orders.models import Address, Order

from ..label_rendering import build_label_context

User = get_user_model()


class BuildLabelContextRecipientTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="comercio@example.com",
            email="comercio@example.com",
            first_name="Comercio",
            last_name="Dueño",
        )

    def _order(self, recipient_name, user=None):
        user = user or self.owner
        address = Address.objects.create(
            user=user, recipient_name=recipient_name, street="Av. Siempre Viva", number="742", city="CABA"
        )
        return Order.objects.create(user=user, address=address)

    def test_el_destinatario_es_el_nombre_de_la_direccion_no_el_duenio_de_la_cuenta(self):
        context = build_label_context(order=self._order("Juan Pérez"))

        self.assertEqual(context["destinatario"], "Juan Pérez")

    def test_direccion_sin_nombre_usa_el_nombre_del_usuario(self):
        context = build_label_context(order=self._order("   "))

        self.assertEqual(context["destinatario"], "Comercio Dueño")

    def test_direccion_y_usuario_sin_nombre_usa_el_email(self):
        user = User.objects.create_user(username="sinnombre@example.com", email="sinnombre@example.com")
        context = build_label_context(order=self._order("", user=user))

        self.assertEqual(context["destinatario"], "sinnombre@example.com")


class BuildLabelContextSenderTests(TestCase):
    """El remitente es el cliente que despacha (la app es multi-cliente),
    nunca un nombre fijo de empresa configurado para todos."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="comercio@example.com",
            email="comercio@example.com",
            first_name="Comercio",
            last_name="Dueño",
        )

    def _order(self, user=None, store_connection=None):
        user = user or self.owner
        address = Address.objects.create(user=user, recipient_name="Juan Pérez", street="Mitre", city="CABA")
        return Order.objects.create(user=user, address=address, store_connection=store_connection)

    def _store(self, name):
        from apps.integrations.models import StoreConnection

        return StoreConnection.objects.create(
            owner=self.owner, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="123", name=name
        )

    def test_pedido_de_tienda_usa_el_nombre_de_la_tienda(self):
        context = build_label_context(order=self._order(store_connection=self._store("Textiles del Litoral")))

        self.assertEqual(context["remitente"], "Textiles del Litoral")

    def test_tienda_sin_nombre_usa_el_nombre_del_cliente(self):
        context = build_label_context(order=self._order(store_connection=self._store("  ")))

        self.assertEqual(context["remitente"], "Comercio Dueño")

    def test_pedido_manual_usa_el_nombre_del_cliente(self):
        context = build_label_context(order=self._order())

        self.assertEqual(context["remitente"], "Comercio Dueño")

    def test_cliente_sin_nombre_usa_el_email(self):
        user = User.objects.create_user(username="sinnombre@example.com", email="sinnombre@example.com")

        context = build_label_context(order=self._order(user=user))

        self.assertEqual(context["remitente"], "sinnombre@example.com")

    def test_nunca_imprime_buspack_por_defecto(self):
        context = build_label_context(order=self._order())

        self.assertNotIn("buspack", context["remitente"].lower())


class BuildLabelContextOrderDataTests(TestCase):
    """Número de pedido de la tienda, dirección completa y datos del envío,
    sin nada sensible para quien manipula el paquete."""

    def setUp(self):
        self.owner = User.objects.create_user(username="comercio@example.com", email="comercio@example.com")

    def _order(self, **order_fields):
        address = Address.objects.create(
            user=self.owner,
            recipient_name="Juan Pérez",
            street="Mitre",
            number="145",
            city="CABA",
            state="Capital Federal",
            postal_code="1428",
            country="Argentina",
            reference="Piso/Depto: 1 · Barrio: Belgrano",
        )
        return Order.objects.create(user=self.owner, address=address, **order_fields)

    def test_pedido_de_tienda_imprime_el_numero_de_la_tienda(self):
        context = build_label_context(order=self._order(external_number="100"))

        self.assertEqual(context["pedido"], "Pedido #100")

    def test_pedido_sin_numero_externo_imprime_el_id_propio(self):
        order = self._order()

        self.assertEqual(build_label_context(order=order)["pedido"], f"Pedido #{order.pk}")

    def test_incluye_piso_y_referencia_de_la_direccion(self):
        context = build_label_context(order=self._order())

        self.assertEqual(context["referencia"], "Piso/Depto: 1 · Barrio: Belgrano")

    def test_incluye_pais_fecha_del_pedido_y_envio(self):
        order = self._order(shipping_option="Correo Argentino a domicilio")
        context = build_label_context(order=order)

        self.assertEqual(context["pais"], "Argentina")
        self.assertEqual(context["envio"], "Correo Argentino a domicilio")
        from django.utils import timezone

        self.assertEqual(context["fecha_pedido"], timezone.localtime(order.created_at).strftime("%d/%m/%Y"))

    def test_envio_sin_opcion_usa_el_transportista(self):
        context = build_label_context(order=self._order(carrier="Andreani"))

        self.assertEqual(context["envio"], "Andreani")

    def test_no_expone_datos_sensibles_del_pedido(self):
        order = self._order(
            contact_email="comprador@example.com",
            contact_phone="1155554444",
            items=[{"name": "campera de cuero", "sku": "", "quantity": 1}],
            description="1x campera de cuero",
        )
        values = " ".join(str(v) for v in build_label_context(order=order).values())

        for sensitive in ("comprador@example.com", "1155554444", "campera de cuero"):
            self.assertNotIn(sensitive, values)
