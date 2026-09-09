"""Tests de rótulos y plantillas (self-service + panel admin).

Sigue el mismo patrón que ``apps/orders/tests.py``: crea usuarios de
prueba, loguea vía JWT y pega contra los endpoints reales.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.orders.models import Address, Order

from .models import Label, LabelTemplate

User = get_user_model()

# PNG 1x1 transparente, válido para pasar la verificación de Pillow.
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YA"
    "AAAASUVORK5CYII="
)
TINY_PNG_DATA_URL = f"data:image/png;base64,{TINY_PNG_B64}"

VALID_DESIGN = {
    "logo": {"left": 6, "top": 5},
    "qr": {"left": 68, "top": 5},
    "remitente": {"left": 6, "top": 22, "text": "Remitente: Juan Pérez"},
    "destinatario": {"left": 6, "top": 38, "text": "Destinatario: María Gómez"},
    "domicilio": {"left": 6, "top": 47, "text": "Domicilio: Av. Siempre Viva 742"},
    "cp": {"left": 6, "top": 56, "text": "CP: 1000"},
    "localidad": {"left": 6, "top": 63, "text": "Localidad/Provincia: CABA"},
    "pedido": {"left": 6, "top": 88, "text": "N° de Pedido: 000123"},
}


def auth_headers_for(user):
    token = RefreshToken.for_user(user)
    return {"HTTP_AUTHORIZATION": f"Bearer {token.access_token}"}


class LabelsSelfServiceTests(APITestCase):
    def setUp(self):
        subscriber_group, _ = Group.objects.get_or_create(name="subscriber")

        self.user = User.objects.create_user(
            username="disenador1@example.com",
            email="disenador1@example.com",
            password="Clave123!",
        )
        self.user.groups.add(subscriber_group)

        self.other_user = User.objects.create_user(
            username="disenador2@example.com",
            email="disenador2@example.com",
            password="Clave123!",
        )
        self.other_user.groups.add(subscriber_group)

        self.address = Address.objects.create(
            user=self.user, street="Av. Siempre Viva", number="742", city="CABA", is_default=True
        )
        self.order = Order.objects.create(user=self.user, address=self.address)

        self.other_address = Address.objects.create(
            user=self.other_user, street="Otra calle", city="Rosario", is_default=True
        )
        self.other_order = Order.objects.create(user=self.other_user, address=self.other_address)

    def _create_label(self, user=None, **overrides):
        payload = {
            "name": "Encomienda Juan",
            "client": "María Gómez",
            "width_cm": "10",
            "height_cm": "15",
            "design": VALID_DESIGN,
        }
        payload.update(overrides)
        return self.client.post(
            "/api/v1/labels/labels/", payload, format="json", **auth_headers_for(user or self.user)
        )

    # --- CRUD básico -----------------------------------------------------

    def test_create_label_valid_design(self):
        response = self._create_label()
        self.assertEqual(response.status_code, 201, response.data)
        label = Label.objects.get(pk=response.data["id"])
        self.assertEqual(label.user, self.user)
        self.assertEqual(label.design["remitente"]["text"], "Remitente: Juan Pérez")

    def test_create_label_invalid_design_returns_400(self):
        response = self._create_label(design={"campo_inventado": {"left": 1, "top": 1}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("design", response.data)

    def test_create_label_design_left_top_out_of_range_returns_400(self):
        response = self._create_label(design={"remitente": {"left": 150, "top": 5}})
        self.assertEqual(response.status_code, 400)

    def test_create_label_width_out_of_range_returns_400(self):
        response = self._create_label(width_cm="100")
        self.assertEqual(response.status_code, 400)
        self.assertIn("width_cm", response.data)

    def test_list_labels_returns_only_own(self):
        self._create_label()
        self._create_label(user=self.other_user)

        response = self.client.get("/api/v1/labels/labels/", **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 200)
        results = response.data["results"] if "results" in response.data else response.data
        self.assertEqual(len(results), 1)

    def test_retrieve_foreign_label_returns_404(self):
        created = self._create_label(user=self.other_user)
        label_id = created.data["id"]

        response = self.client.get(
            f"/api/v1/labels/labels/{label_id}/", **auth_headers_for(self.user)
        )
        self.assertEqual(response.status_code, 404)

    def test_update_own_label(self):
        created = self._create_label()
        label_id = created.data["id"]

        response = self.client.patch(
            f"/api/v1/labels/labels/{label_id}/",
            {"name": "Encomienda Juan (editado)"},
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["name"], "Encomienda Juan (editado)")

    def test_delete_is_soft_delete(self):
        created = self._create_label()
        label_id = created.data["id"]

        response = self.client.delete(
            f"/api/v1/labels/labels/{label_id}/", **auth_headers_for(self.user)
        )
        self.assertEqual(response.status_code, 204)

        label = Label.objects.get(pk=label_id)
        self.assertFalse(label.is_active)

        list_response = self.client.get("/api/v1/labels/labels/", **auth_headers_for(self.user))
        results = (
            list_response.data["results"]
            if "results" in list_response.data
            else list_response.data
        )
        self.assertEqual(len(results), 0)

    def test_cannot_associate_label_with_foreign_order(self):
        response = self._create_label(order=self.other_order.pk)
        self.assertEqual(response.status_code, 400)
        self.assertIn("order", response.data)

    def test_can_associate_label_with_own_order(self):
        response = self._create_label(order=self.order.pk)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["order"], self.order.pk)

    def test_duplicate_creates_independent_copy(self):
        created = self._create_label(order=self.order.pk)
        label_id = created.data["id"]

        response = self.client.post(
            f"/api/v1/labels/labels/{label_id}/duplicate/", **auth_headers_for(self.user)
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotEqual(response.data["id"], label_id)
        self.assertEqual(response.data["name"], "Encomienda Juan (copia)")

        original = Label.objects.get(pk=label_id)
        copy = Label.objects.get(pk=response.data["id"])
        self.assertEqual(original.name, "Encomienda Juan")
        self.assertEqual(copy.design, original.design)
        self.assertEqual(copy.order_id, original.order_id)

    def test_logo_as_data_url_is_decoded_to_image_field(self):
        response = self._create_label(logo=TINY_PNG_DATA_URL)
        self.assertEqual(response.status_code, 201, response.data)
        label = Label.objects.get(pk=response.data["id"])
        self.assertTrue(bool(label.logo))
        self.assertTrue(response.data["logo"].startswith("http"))

    def test_invalid_data_url_logo_returns_400(self):
        response = self._create_label(logo="data:image/png;base64,esto-no-es-base64-valido")
        self.assertEqual(response.status_code, 400)
        self.assertIn("logo", response.data)

    def test_labels_require_authentication(self):
        response = self.client.get("/api/v1/labels/labels/")
        self.assertEqual(response.status_code, 401)


class LabelTemplatesTests(APITestCase):
    def setUp(self):
        subscriber_group, _ = Group.objects.get_or_create(name="subscriber")
        admin_group, _ = Group.objects.get_or_create(name="admin")

        self.admin_user = User.objects.create_user(
            username="admin_labels@example.com",
            email="admin_labels@example.com",
            password="Clave123!",
            is_staff=True,
        )
        self.admin_user.groups.add(admin_group)

        self.user = User.objects.create_user(
            username="disenador3@example.com",
            email="disenador3@example.com",
            password="Clave123!",
        )
        self.user.groups.add(subscriber_group)

    def test_non_admin_cannot_create_template(self):
        response = self.client.post(
            "/api/v1/labels/templates/",
            {"name": "Plantilla base", "width_cm": "10", "height_cm": "15", "design": VALID_DESIGN},
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_public_template(self):
        response = self.client.post(
            "/api/v1/labels/templates/",
            {
                "name": "Plantilla base",
                "width_cm": "10",
                "height_cm": "15",
                "design": VALID_DESIGN,
                "is_public": True,
            },
            format="json",
            **auth_headers_for(self.admin_user),
        )
        self.assertEqual(response.status_code, 201, response.data)

    def test_any_authenticated_user_can_list_public_templates(self):
        LabelTemplate.objects.create(
            name="Plantilla pública",
            is_public=True,
            width_cm=10,
            height_cm=15,
            design=VALID_DESIGN,
        )

        response = self.client.get("/api/v1/labels/templates/", **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 200)
        results = response.data["results"] if "results" in response.data else response.data
        self.assertEqual(len(results), 1)


class AdminLabelsTests(APITestCase):
    """Rótulos de todos los usuarios (panel admin, labels.view_all) y el
    rastro de auditoría que deja crear/editar/borrar un rótulo."""

    def setUp(self):
        subscriber_group, _ = Group.objects.get_or_create(name="subscriber")
        admin_group, _ = Group.objects.get_or_create(name="admin")

        self.admin_user = User.objects.create_user(
            username="admin_labels2@example.com",
            email="admin_labels2@example.com",
            password="Clave123!",
            is_staff=True,
        )
        self.admin_user.groups.add(admin_group)

        self.user = User.objects.create_user(
            username="disenador4@example.com",
            email="disenador4@example.com",
            password="Clave123!",
        )
        self.user.groups.add(subscriber_group)

        self.label = Label.objects.create(
            user=self.user,
            name="Encomienda X",
            client="Cliente X",
            width_cm=10,
            height_cm=15,
            design=VALID_DESIGN,
        )

    def test_non_admin_receives_403_on_admin_listing(self):
        response = self.client.get("/api/v1/labels/admin/", **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 403)

    def test_admin_receives_200_with_all_labels(self):
        response = self.client.get("/api/v1/labels/admin/", **auth_headers_for(self.admin_user))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["pagination"]["count"], 1)
        self.assertEqual(response.data["results"][0]["user_email"], self.user.email)

    def test_create_label_leaves_auditlog(self):
        from apps.audit.models import AuditLog

        response = self.client.post(
            "/api/v1/labels/labels/",
            {
                "name": "Encomienda Y",
                "client": "Cliente Y",
                "width_cm": "10",
                "height_cm": "15",
                "design": VALID_DESIGN,
            },
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 201, response.data)
        log = AuditLog.objects.filter(action="label.create").latest("created_at")
        self.assertEqual(log.actor_id, self.user.id)

    def test_delete_label_leaves_auditlog(self):
        from apps.audit.models import AuditLog

        response = self.client.delete(
            f"/api/v1/labels/labels/{self.label.pk}/", **auth_headers_for(self.user)
        )
        self.assertEqual(response.status_code, 204)
        log = AuditLog.objects.filter(
            action="label.delete", target_id=str(self.label.pk)
        ).latest("created_at")
        self.assertEqual(log.actor_id, self.user.id)


class PercentToCanvasXyTests(SimpleTestCase):
    """La conversión porcentaje -> puntos de ``apps.labels.rendering``: si
    sale mal, el rótulo imprime todo al revés y no se nota hasta el papel.
    """

    def test_top_0_percent_is_the_pdf_top_edge_and_100_percent_is_the_bottom_edge(self):
        from .rendering import CM_TO_POINTS, percent_to_canvas_xy

        width_cm, height_cm = 10, 15

        _, y_top = percent_to_canvas_xy(0, 0, width_cm, height_cm)
        self.assertAlmostEqual(y_top, height_cm * CM_TO_POINTS)

        _, y_bottom = percent_to_canvas_xy(0, 100, width_cm, height_cm)
        self.assertAlmostEqual(y_bottom, 0)


class BuildBarcodeDrawingTests(SimpleTestCase):
    """Un EAN-13 con otra cantidad de dígitos no es un código válido:
    ReportLab lo dibujaría igual (rellenando/truncando por su cuenta), sin
    avisar, y termina en una etiqueta que ningún lector reconoce.
    """

    def test_ean13_with_wrong_digit_count_raises_value_error(self):
        from .rendering import build_barcode_drawing

        with self.assertRaises(ValueError):
            build_barcode_drawing("12345", "ean13", 8, 1.5)


class LabelBatchForeignOrderTests(APITestCase):
    """La puerta por la que se filtrarían datos de otro cliente: un
    ``order_id`` ajeno en el lote tiene que cortar en seco, sin crear
    ningún ``Document`` a medio camino."""

    def setUp(self):
        from apps.documents.models import Document

        self.Document = Document
        subscriber_group, _ = Group.objects.get_or_create(name="subscriber")

        self.user = User.objects.create_user(
            username="loteusuario@example.com",
            email="loteusuario@example.com",
            password="Clave123!",
        )
        self.user.groups.add(subscriber_group)

        self.other_user = User.objects.create_user(
            username="loteotro@example.com",
            email="loteotro@example.com",
            password="Clave123!",
        )
        self.other_user.groups.add(subscriber_group)

        other_address = Address.objects.create(
            user=self.other_user,
            street="Calle Ajena",
            number="999",
            city="Córdoba",
            state="Córdoba",
            postal_code="5000",
        )
        self.foreign_order = Order.objects.create(user=self.other_user, address=other_address)

        self.template = LabelTemplate.objects.create(
            name="Plantilla lote",
            owner=self.user,
            is_public=True,
            width_cm=10,
            height_cm=15,
            design=VALID_DESIGN,
        )

    def test_batch_with_foreign_order_id_returns_400_and_creates_no_document(self):
        response = self.client.post(
            "/api/v1/labels/batch/",
            {
                "template_id": self.template.pk,
                "order_ids": [self.foreign_order.pk],
                "output": "pdf",
            },
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.Document.objects.count(), 0)
