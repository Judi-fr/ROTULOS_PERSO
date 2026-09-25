"""Ajustes de rótulos por tienda: ``PATCH /api/v1/integrations/stores/<id>/settings/``
(remitente, logo, plantilla preferida) y su uso al imprimir."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.integrations.models import StoreConnection
from apps.labels.label_rendering import build_label_context
from apps.labels.models import LabelTemplate
from apps.orders.models import Address, Order
from apps.orders.tests.test_orders import auth_headers_for

User = get_user_model()


class StoreSenderTests(APITestCase):
    def setUp(self):
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(
            username="vendedor@example.com", email="vendedor@example.com", password="Clave123!"
        )
        self.user.groups.add(subscriber)
        self.other = User.objects.create_user(
            username="ajeno@example.com", email="ajeno@example.com", password="Clave123!"
        )
        self.other.groups.add(subscriber)
        self.store = StoreConnection.objects.create(
            owner=self.user,
            platform=StoreConnection.Platform.TIENDANUBE,
            external_store_id="111",
            name="Tienda A",
        )

    def _patch(self, payload, user=None, store=None):
        store = store or self.store
        return self.client.patch(
            f"/api/v1/integrations/stores/{store.pk}/settings/",
            payload,
            format="json",
            **auth_headers_for(user or self.user),
        )

    def test_guarda_el_remitente_de_la_tienda(self):
        response = self._patch(
            {
                "sender_name": "Mi Tienda SRL",
                "sender_address": "Av. Siempre Viva 742, CABA",
                "sender_phone": "11 5555-5555",
            }
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.store.refresh_from_db()
        self.assertEqual(self.store.sender_name, "Mi Tienda SRL")
        self.assertEqual(self.store.sender_address, "Av. Siempre Viva 742, CABA")
        self.assertEqual(self.store.sender_phone, "11 5555-5555")
        self.assertEqual(response.data["sender_name"], "Mi Tienda SRL")

    def test_queda_auditado_sin_exponer_el_token(self):
        self._patch({"sender_name": "Mi Tienda SRL"})

        log = AuditLog.objects.get(action="store.update")
        self.assertEqual(log.actor, self.user)
        self.assertEqual(log.changes["sender_name"], {"from": "", "to": "Mi Tienda SRL"})
        self.assertNotIn("access_token", log.changes)

    def test_no_se_puede_editar_la_tienda_de_otro(self):
        response = self._patch({"sender_name": "Trucho"}, user=self.other)

        self.assertEqual(response.status_code, 404)
        self.store.refresh_from_db()
        self.assertEqual(self.store.sender_name, "")

    def test_el_endpoint_no_deja_tocar_otros_campos(self):
        response = self._patch({"sender_name": "Mi Tienda SRL", "status": "revoked", "name": "Otro"})

        self.assertEqual(response.status_code, 200, response.data)
        self.store.refresh_from_db()
        self.assertEqual(self.store.status, StoreConnection.Status.ACTIVE)
        self.assertEqual(self.store.name, "Tienda A")


class StoreSenderLabelContextTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="vendedor2@example.com", email="vendedor2@example.com", password="Clave123!"
        )
        self.store = StoreConnection.objects.create(
            owner=self.user,
            platform=StoreConnection.Platform.TIENDANUBE,
            external_store_id="222",
            name="Tienda B",
        )
        self.address = Address.objects.create(
            user=self.user,
            origin=Address.Origin.SHIPMENT,
            recipient_name="María Gómez",
            street="Corrientes",
            number="1000",
            city="CABA",
        )
        self.order = Order.objects.create(
            user=self.user, address=self.address, store_connection=self.store, external_id="o1"
        )

    def test_sin_remitente_configurado_usa_el_nombre_de_la_tienda(self):
        context = build_label_context(order=self.order)

        self.assertEqual(context["remitente"], "Tienda B")
        self.assertEqual(context["remitente_domicilio"], "")
        self.assertEqual(context["remitente_telefono"], "")

    def test_el_remitente_de_la_tienda_gana_sobre_el_nombre(self):
        self.store.sender_name = "Mi Tienda SRL"
        self.store.sender_address = "Av. Siempre Viva 742, CABA"
        self.store.sender_phone = "11 5555-5555"
        self.store.save()

        context = build_label_context(order=self.order)

        self.assertEqual(context["remitente"], "Mi Tienda SRL")
        self.assertEqual(context["remitente_domicilio"], "Av. Siempre Viva 742, CABA")
        self.assertEqual(context["remitente_telefono"], "11 5555-5555")
        # El destinatario sigue siendo el comprador, nunca el remitente.
        self.assertEqual(context["destinatario"], "María Gómez")

    def test_un_pedido_sin_tienda_no_trae_remitente_de_tienda(self):
        own_order = Order.objects.create(user=self.user, address=self.address)

        context = build_label_context(order=own_order)

        self.assertEqual(context["remitente_domicilio"], "")
        self.assertEqual(context["remitente_telefono"], "")


class StoreLogoAndTemplateTests(APITestCase):
    """Logo y plantilla preferida de la tienda: se guardan igual que el
    remitente y se usan al generar los rótulos de sus pedidos."""

    def setUp(self):
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(
            username="vendedor4@example.com", email="vendedor4@example.com", password="Clave123!"
        )
        self.user.groups.add(subscriber)
        self.other = User.objects.create_user(
            username="ajeno2@example.com", email="ajeno2@example.com", password="Clave123!"
        )
        self.store = StoreConnection.objects.create(
            owner=self.user,
            platform=StoreConnection.Platform.TIENDANUBE,
            external_store_id="444",
            name="Tienda D",
        )
        self.own_template = LabelTemplate.objects.create(
            owner=self.user, name="Mi plantilla", width_cm=10, height_cm=15, design={}
        )
        self.foreign_template = LabelTemplate.objects.create(
            owner=self.other, name="Ajena", width_cm=10, height_cm=15, design={}
        )

    def _patch(self, payload, fmt="json"):
        return self.client.patch(
            f"/api/v1/integrations/stores/{self.store.pk}/settings/",
            payload,
            format=fmt,
            **auth_headers_for(self.user),
        )

    def test_guarda_la_plantilla_preferida_propia(self):
        response = self._patch({"default_template": self.own_template.pk})

        self.assertEqual(response.status_code, 200, response.data)
        self.store.refresh_from_db()
        self.assertEqual(self.store.default_template, self.own_template)
        self.assertEqual(response.data["default_template_name"], "Mi plantilla")

    def test_rechaza_una_plantilla_de_otro_usuario(self):
        response = self._patch({"default_template": self.foreign_template.pk})

        self.assertEqual(response.status_code, 400)
        self.store.refresh_from_db()
        self.assertIsNone(self.store.default_template)

    def test_guarda_y_borra_el_logo(self):
        png = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        response = self._patch({"logo": png})

        self.assertEqual(response.status_code, 200, response.data)
        self.store.refresh_from_db()
        self.assertTrue(self.store.logo)

        self.assertEqual(self._patch({"logo": None}).status_code, 200)
        self.store.refresh_from_db()
        self.assertFalse(self.store.logo)

    def test_si_se_borra_la_plantilla_la_tienda_sigue_conectada(self):
        self._patch({"default_template": self.own_template.pk})

        self.own_template.delete()

        self.store.refresh_from_db()
        self.assertIsNone(self.store.default_template)
        self.assertEqual(self.store.status, StoreConnection.Status.ACTIVE)


class StorePrinterDensityTests(APITestCase):
    """Densidad de la impresora térmica de la tienda (``label_printer_dpmm``).

    Se guarda acá y no se pregunta en cada impresión: un comerciante con una
    Zebra de 300 dpi la configura una vez (ver apps.labels.zpl y
    ``LabelBatchView._resolve_dpmm``).
    """

    def setUp(self):
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(
            username="impresora@example.com", email="impresora@example.com", password="Clave123!"
        )
        self.user.groups.add(subscriber)
        self.store = StoreConnection.objects.create(
            owner=self.user,
            platform=StoreConnection.Platform.TIENDANUBE,
            external_store_id="777",
            name="Tienda con térmica",
        )

    def _patch(self, payload):
        return self.client.patch(
            f"/api/v1/integrations/stores/{self.store.pk}/settings/",
            payload,
            format="json",
            **auth_headers_for(self.user),
        )

    def test_arranca_sin_configurar(self):
        """Null = sin configurar, igual que default_template: el lote cae a
        203 dpi en vez de a un valor que nadie eligió."""
        self.assertIsNone(self.store.label_printer_dpmm)

    def test_guarda_la_densidad(self):
        response = self._patch({"label_printer_dpmm": 12})

        self.assertEqual(response.status_code, 200, response.data)
        self.store.refresh_from_db()
        self.assertEqual(self.store.label_printer_dpmm, 12)
        self.assertEqual(response.data["label_printer_dpmm"], 12)

    def test_se_puede_volver_a_dejar_sin_configurar(self):
        self.store.label_printer_dpmm = 12
        self.store.save()

        response = self._patch({"label_printer_dpmm": None})

        self.assertEqual(response.status_code, 200, response.data)
        self.store.refresh_from_db()
        self.assertIsNone(self.store.label_printer_dpmm)

    def test_rechaza_una_densidad_que_no_existe(self):
        response = self._patch({"label_printer_dpmm": 7})

        self.assertEqual(response.status_code, 400)
        self.assertIn("label_printer_dpmm", response.data)
        self.store.refresh_from_db()
        self.assertIsNone(self.store.label_printer_dpmm)

    def test_queda_en_la_auditoria(self):
        from apps.audit.models import AuditLog

        self._patch({"label_printer_dpmm": 12})

        entrada = AuditLog.objects.filter(action="store.update").latest("created_at")
        self.assertIn("label_printer_dpmm", entrada.changes)
        self.assertEqual(entrada.changes["label_printer_dpmm"]["to"], "12")

    def test_no_se_puede_configurar_la_tienda_de_otro(self):
        otro = User.objects.create_user(
            username="ajeno3@example.com", email="ajeno3@example.com", password="Clave123!"
        )
        otro.groups.add(Group.objects.get(name="subscriber"))
        response = self.client.patch(
            f"/api/v1/integrations/stores/{self.store.pk}/settings/",
            {"label_printer_dpmm": 12},
            format="json",
            **auth_headers_for(otro),
        )
        self.assertEqual(response.status_code, 404)
        self.store.refresh_from_db()
        self.assertIsNone(self.store.label_printer_dpmm)
