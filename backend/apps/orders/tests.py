"""Tests de direcciones y pedidos propios (self-service).

Sigue el mismo patrón que apps/accounts/tests.py: crea usuarios de prueba,
loguea vía JWT y pega contra los endpoints reales.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Address, Order

User = get_user_model()


def auth_headers_for(user):
    token = RefreshToken.for_user(user)
    return {"HTTP_AUTHORIZATION": f"Bearer {token.access_token}"}


class OrdersSelfServiceTests(APITestCase):
    def setUp(self):
        subscriber_group, _ = Group.objects.get_or_create(name="subscriber")

        self.user = User.objects.create_user(
            username="cliente1", email="cliente1@example.com", password="Clave123!"
        )
        self.user.groups.add(subscriber_group)

        self.other_user = User.objects.create_user(
            username="cliente2", email="cliente2@example.com", password="Clave123!"
        )
        self.other_user.groups.add(subscriber_group)

        self.address = Address.objects.create(
            user=self.user,
            label="Casa",
            street="Av. Siempre Viva",
            number="742",
            city="Springfield",
            is_default=True,
        )

    # --- Direcciones -------------------------------------------------

    def test_list_addresses_returns_only_own(self):
        Address.objects.create(user=self.other_user, street="Otra calle", city="Otra ciudad")

        response = self.client.get("/api/v1/addresses/", **auth_headers_for(self.user))

        self.assertEqual(response.status_code, 200)
        results = response.data["results"] if "results" in response.data else response.data
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["street"], "Av. Siempre Viva")

    def test_create_address_assigns_current_user(self):
        payload = {"street": "Calle Falsa", "number": "123", "city": "CABA"}
        response = self.client.post(
            "/api/v1/addresses/", payload, **auth_headers_for(self.user)
        )

        self.assertEqual(response.status_code, 201, response.data)
        address = Address.objects.get(pk=response.data["id"])
        self.assertEqual(address.user, self.user)

    def test_setting_new_default_address_unsets_previous(self):
        second = Address.objects.create(
            user=self.user, street="Nueva default", city="CABA", is_default=False
        )

        response = self.client.patch(
            f"/api/v1/addresses/{second.pk}/",
            {"is_default": True},
            **auth_headers_for(self.user),
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.address.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(self.address.is_default)
        self.assertTrue(second.is_default)

    def test_cannot_delete_address_with_orders(self):
        Order.objects.create(user=self.user, address=self.address)

        response = self.client.delete(
            f"/api/v1/addresses/{self.address.pk}/", **auth_headers_for(self.user)
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(Address.objects.filter(pk=self.address.pk).exists())

    def test_cannot_use_another_users_address(self):
        foreign_address = Address.objects.create(
            user=self.other_user, street="Ajena", city="Otra ciudad"
        )

        response = self.client.post(
            "/api/v1/orders/",
            {"address_id": foreign_address.pk, "description": "Paquete"},
            **auth_headers_for(self.user),
        )

        self.assertEqual(response.status_code, 400)

    # --- Pedidos -------------------------------------------------------

    def test_create_order_sets_initial_status_and_event(self):
        response = self.client.post(
            "/api/v1/orders/",
            {"address_id": self.address.pk, "description": "Caja mediana"},
            **auth_headers_for(self.user),
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], Order.Status.CREATED)
        self.assertTrue(response.data["is_cancellable"])
        order = Order.objects.get(pk=response.data["id"])
        self.assertEqual(order.status_events.count(), 1)
        self.assertEqual(order.status_events.first().status, Order.Status.CREATED)

    def test_list_orders_returns_only_own(self):
        Order.objects.create(user=self.user, address=self.address)
        other_address = Address.objects.create(
            user=self.other_user, street="Otra calle", city="Otra ciudad"
        )
        Order.objects.create(user=self.other_user, address=other_address)

        response = self.client.get("/api/v1/orders/", **auth_headers_for(self.user))

        self.assertEqual(response.status_code, 200)
        results = response.data["results"] if "results" in response.data else response.data
        self.assertEqual(len(results), 1)

    def test_cancel_own_order(self):
        order = Order.objects.create(user=self.user, address=self.address)

        response = self.client.post(
            f"/api/v1/orders/{order.pk}/cancel/", **auth_headers_for(self.user)
        )

        self.assertEqual(response.status_code, 200, response.data)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.CANCELLED)
        self.assertEqual(
            list(order.status_events.values_list("status", flat=True)),
            [Order.Status.CREATED, Order.Status.CANCELLED],
        )

    def test_cannot_cancel_dispatched_order(self):
        order = Order.objects.create(
            user=self.user, address=self.address, status=Order.Status.DISPATCHED
        )

        response = self.client.post(
            f"/api/v1/orders/{order.pk}/cancel/", **auth_headers_for(self.user)
        )

        self.assertEqual(response.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.DISPATCHED)

    def test_cannot_access_another_users_order(self):
        other_address = Address.objects.create(
            user=self.other_user, street="Otra calle", city="Otra ciudad"
        )
        foreign_order = Order.objects.create(user=self.other_user, address=other_address)

        response = self.client.get(
            f"/api/v1/orders/{foreign_order.pk}/", **auth_headers_for(self.user)
        )

        self.assertEqual(response.status_code, 404)

    def test_orders_require_authentication(self):
        response = self.client.get("/api/v1/orders/")
        self.assertEqual(response.status_code, 401)


class AdminOrdersTests(APITestCase):
    """Pedidos de todos los usuarios (panel admin, orders.view_all) y el
    rastro de auditoría que deja crear/cancelar un pedido."""

    def setUp(self):
        subscriber_group, _ = Group.objects.get_or_create(name="subscriber")
        admin_group, _ = Group.objects.get_or_create(name="admin")

        self.admin_user = User.objects.create_user(
            username="admin_orders@example.com",
            email="admin_orders@example.com",
            password="Clave123!",
            is_staff=True,
        )
        self.admin_user.groups.add(admin_group)

        self.user = User.objects.create_user(
            username="cliente_orders@example.com",
            email="cliente_orders@example.com",
            password="Clave123!",
        )
        self.user.groups.add(subscriber_group)

        self.address = Address.objects.create(
            user=self.user, street="Calle 1", city="CABA", is_default=True
        )
        self.order = Order.objects.create(user=self.user, address=self.address)

    def test_no_admin_recibe_403(self):
        response = self.client.get("/api/v1/admin/orders/", **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 403)

    def test_admin_recibe_200_con_pedidos_de_todos(self):
        response = self.client.get("/api/v1/admin/orders/", **auth_headers_for(self.admin_user))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["pagination"]["count"], 1)
        self.assertEqual(response.data["results"][0]["user_email"], self.user.email)

    def test_crear_pedido_deja_auditlog_order_create(self):
        from apps.audit.models import AuditLog

        response = self.client.post(
            "/api/v1/orders/",
            {"address_id": self.address.pk, "description": "Otro"},
            **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 201, response.data)
        log = AuditLog.objects.filter(action="order.create").latest("created_at")
        self.assertEqual(log.actor_id, self.user.id)

    def test_cancelar_pedido_deja_un_solo_auditlog_de_cancelacion(self):
        from apps.audit.models import AuditLog

        response = self.client.post(
            f"/api/v1/orders/{self.order.pk}/cancel/", **auth_headers_for(self.user)
        )
        self.assertEqual(response.status_code, 200, response.data)

        cancel_logs = AuditLog.objects.filter(action="order.cancel", target_id=str(self.order.pk))
        self.assertEqual(cancel_logs.count(), 1)
        self.assertEqual(cancel_logs.first().actor_id, self.user.id)
        # El _skip_status_audit del modelo evita duplicar con order.status_change.
        self.assertFalse(
            AuditLog.objects.filter(
                action="order.status_change", target_id=str(self.order.pk)
            ).exists()
        )

    def test_edicion_manual_de_estado_deja_order_status_change_sin_actor(self):
        from apps.audit.models import AuditLog

        self.order.status = Order.Status.PREPARING
        self.order.save(update_fields=["status", "updated_at"])

        log = AuditLog.objects.filter(action="order.status_change").latest("created_at")
        self.assertIsNone(log.actor_id)
        self.assertEqual(log.changes["status"], {"from": Order.Status.CREATED, "to": Order.Status.PREPARING})


class OrderImportTests(APITestCase):
    """Importación CSV/Excel (story 21): lo que se rompe en silencio si
    sale mal es (1) reimportar duplica pedidos y (2) una fila inválida
    tumba el resto del archivo en vez de saltearse."""

    CSV_HEADERS = [
        "Destinatario",
        "Domicilio",
        "Numero",
        "Ciudad",
        "Provincia",
        "CP",
        "Referencia",
        "Descripcion",
        "ID Externo",
    ]
    MAPPING = {
        "destinatario": "Destinatario",
        "domicilio": "Domicilio",
        "numero": "Numero",
        "ciudad": "Ciudad",
        "provincia": "Provincia",
        "cp": "CP",
        "referencia": "Referencia",
        "descripcion": "Descripcion",
        "external_id": "ID Externo",
    }

    def setUp(self):
        operator_group, _ = Group.objects.get_or_create(name="operator")
        self.user = User.objects.create_user(
            username="operador1@example.com",
            email="operador1@example.com",
            password="Clave123!",
        )
        self.user.groups.add(operator_group)

    def _upload_csv(self, csv_text):
        from django.core.files.uploadedfile import SimpleUploadedFile

        file_obj = SimpleUploadedFile(
            "pedidos.csv", csv_text.encode("utf-8"), content_type="text/csv"
        )
        response = self.client.post(
            "/api/v1/orders/imports/", {"file": file_obj}, **auth_headers_for(self.user)
        )
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def _confirm(self, import_id, mapping=None):
        response = self.client.post(
            f"/api/v1/orders/imports/{import_id}/confirm/",
            {"mapping": mapping or self.MAPPING},
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_importar_el_mismo_archivo_dos_veces_no_duplica_pedidos(self):
        csv_text = (
            ";".join(self.CSV_HEADERS)
            + "\n"
            + "Juan Pérez;Av. Siempre Viva;742;CABA;Buenos Aires;1000;Timbre azul;Paquete;PED-0001"
        )

        first_import_id = self._upload_csv(csv_text)
        first_result = self._confirm(first_import_id)
        self.assertEqual(first_result["imported_count"], 1)
        self.assertEqual(first_result["skipped_count"], 0)
        self.assertEqual(Order.objects.filter(user=self.user).count(), 1)

        # Mismo archivo, misma sesión de importación (mismo external_id):
        # la segunda vez no debe crear un pedido nuevo, tiene que saltearlo.
        second_import_id = self._upload_csv(csv_text)
        second_result = self._confirm(second_import_id)
        self.assertEqual(second_result["imported_count"], 0)
        self.assertEqual(second_result["skipped_count"], 1)
        self.assertEqual(Order.objects.filter(user=self.user).count(), 1)

    def test_fila_invalida_no_aborta_el_resto_y_reporta_su_numero(self):
        csv_text = (
            ";".join(self.CSV_HEADERS)
            + "\n"
            # Fila 2 (primera de datos): válida.
            + "Juan Pérez;Av. Siempre Viva;742;CABA;Buenos Aires;1000;Timbre azul;Paquete;PED-0002\n"
            # Fila 3: sin domicilio ni ciudad -> inválida, tiene que saltearse
            # y reportarse con su número de fila, sin tumbar la fila 2.
            + "María Gómez;;;;;;;Otro paquete;PED-0003"
        )

        import_id = self._upload_csv(csv_text)
        result = self._confirm(import_id)

        self.assertEqual(result["imported_count"], 1)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["fila"], 3)
        self.assertEqual(Order.objects.filter(user=self.user).count(), 1)
