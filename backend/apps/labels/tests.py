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

from .models import Label, LabelSequence, LabelTemplate

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

    def test_create_label_with_unknown_rule_op_returns_400(self):
        # Historia 28: design["rules"] se valida igual que el resto del
        # design — un op inventado corta en seco, nunca llega al render.
        design = dict(VALID_DESIGN)
        design["rules"] = [
            {
                "when": {"field": "localidad", "op": "es_igual_a", "value": "CABA"},
                "then": {"action": "hide", "target": "qr"},
            }
        ]
        response = self._create_label(design=design)
        self.assertEqual(response.status_code, 400)
        self.assertIn("design", response.data)


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

    def test_non_admin_can_create_own_private_template_but_not_a_public_one(self):
        # labels.template_create (self-service, los cuatro roles) alcanza
        # para una plantilla PROPIA privada; marcarla pública sigue
        # exigiendo labels.manage_templates (solo admin) — antes, cualquiera
        # de las dos cosas exigía ser admin.
        response = self.client.post(
            "/api/v1/labels/templates/",
            {"name": "Plantilla base", "width_cm": "10", "height_cm": "15", "design": VALID_DESIGN},
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data["is_public"])
        self.assertEqual(response.data["owner"], self.user.pk)

        response = self.client.post(
            "/api/v1/labels/templates/",
            {
                "name": "Plantilla pública trucha",
                "width_cm": "10",
                "height_cm": "15",
                "design": VALID_DESIGN,
                "is_public": True,
            },
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
        # No se asume que sea la ÚNICA pública: la plantilla de sistema
        # sembrada por 0003_seed_default_label_template también es pública
        # y activa, así que también aparece acá.
        self.assertIn("Plantilla pública", [t["name"] for t in results])


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


class ComputeA4GridTests(SimpleTestCase):
    """Cuántos rótulos entran por hoja A4 y en qué posición: si esta cuenta
    sale mal, el PDF por lote recorta etiquetas contra el borde de la hoja
    y no se nota hasta que salen impresas."""

    def test_10x15_label_fits_two_per_a4_sheet_within_margins(self):
        from .rendering import compute_a4_grid

        margin_cm = 1.0
        grid = compute_a4_grid(10, 15, margin_cm=margin_cm, gap_cm=0.5)

        self.assertEqual(grid["per_page"], 2)
        self.assertEqual(len(grid["positions"]), 2)

        for x_cm, y_cm in grid["positions"]:
            self.assertGreaterEqual(x_cm, margin_cm)
            self.assertGreaterEqual(y_cm, margin_cm)
            self.assertLessEqual(x_cm + 10, grid["page_width_cm"] - margin_cm + 1e-9)
            self.assertLessEqual(y_cm + 15, grid["page_height_cm"] - margin_cm + 1e-9)


class LabelSequenceTests(APITestCase):
    """Numeración secuencial de {{secuencia}} (Historia 29)."""

    def setUp(self):
        subscriber_group, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(
            username="secuencia@example.com",
            email="secuencia@example.com",
            password="Clave123!",
        )
        self.user.groups.add(subscriber_group)

    def test_next_value_is_consecutive(self):
        first = LabelSequence.next_value(self.user, key="envios")
        second = LabelSequence.next_value(self.user, key="envios")
        third = LabelSequence.next_value(self.user, key="envios")
        self.assertEqual([first, second, third], ["000001", "000002", "000003"])

    def test_next_value_applies_prefix_and_padding(self):
        LabelSequence.objects.create(owner=self.user, key="cortos", prefix="BP-", padding=3)
        self.assertEqual(LabelSequence.next_value(self.user, key="cortos"), "BP-001")
        self.assertEqual(LabelSequence.next_value(self.user, key="cortos"), "BP-002")

    def test_preview_does_not_consume_sequence(self):
        from .rendering import build_computed_context

        seq = LabelSequence.objects.create(owner=self.user, key="default", current=5)

        computed = build_computed_context(owner=self.user, is_preview=True)
        value = computed("secuencia")

        self.assertEqual(value, "000001")  # valor de MUESTRA, no current+1
        seq.refresh_from_db()
        self.assertEqual(seq.current, 5)  # el contador real no se tocó


class ComputedVariablesTests(SimpleTestCase):
    """Variables calculadas del render (Historia 29): fecha/hora, bulto(s)
    y prioridad frente a un dato real del pedido del mismo nombre."""

    def test_real_order_field_wins_over_computed_variable_with_same_name(self):
        from .rendering import _replace_placeholders, build_computed_context

        # El pedido ya trae un campo "fecha" propio: tiene que ganarle a la
        # fecha de emisión calculada, aunque el marcador sea {{fecha}}.
        context = {"fecha": "01/01/2000"}
        computed = build_computed_context()
        self.assertEqual(_replace_placeholders("{{fecha}}", context, computed), "01/01/2000")

    def test_fecha_hora_and_custom_format_markers(self):
        from datetime import datetime

        from .rendering import _replace_placeholders, build_computed_context

        now = datetime(2026, 3, 5, 14, 30)
        computed = build_computed_context(now=now)
        self.assertEqual(_replace_placeholders("{{fecha}}", {}, computed), "05/03/2026")
        self.assertEqual(_replace_placeholders("{{hora}}", {}, computed), "14:30")
        self.assertEqual(_replace_placeholders("{{fecha_hora}}", {}, computed), "05/03/2026 14:30")
        self.assertEqual(
            _replace_placeholders("{{fecha:%Y-%m-%d}}", {}, computed), "2026-03-05"
        )

    def test_unknown_marker_resolves_to_empty_string(self):
        from .rendering import _replace_placeholders, build_computed_context

        computed = build_computed_context()
        self.assertEqual(_replace_placeholders("{{campo_inexistente}}", {}, computed), "")


class DesignRulesTests(SimpleTestCase):
    """Evaluación de design["rules"] (Historia 28) sobre una copia del
    diseño — el guardado en la base nunca se toca."""

    def test_hide_rule_removes_field_from_effective_design_and_leaves_saved_design_intact(self):
        from .rendering import apply_design_rules

        design = {
            "qr": {"left": 68, "top": 5},
            "localidad": {"left": 6, "top": 63, "text": "CABA"},
            "rules": [
                {
                    "when": {"field": "provincia", "op": "equals", "value": "Buenos Aires"},
                    "then": {"action": "hide", "target": "qr"},
                }
            ],
        }
        context = {"provincia": "Buenos Aires"}

        effective = apply_design_rules(design, context, None)

        self.assertNotIn("qr", effective)
        self.assertIn("qr", design)  # el design guardado no se modifica

    def test_last_rule_wins_when_two_rules_touch_the_same_target(self):
        from .rendering import apply_design_rules

        design = {
            "qr": {"left": 68, "top": 5},
            "rules": [
                {"when": {"field": "x", "op": "empty"}, "then": {"action": "hide", "target": "qr"}},
                {"when": {"field": "x", "op": "empty"}, "then": {"action": "show", "target": "qr"}},
            ],
        }

        effective = apply_design_rules(design, {}, None)

        self.assertIn("qr", effective)

    def test_set_text_and_move_actions(self):
        from .rendering import apply_design_rules

        design = {
            "pedido": {"left": 6, "top": 88, "text": "N° de Pedido: 000123"},
            "rules": [
                {
                    "when": {"field": "estado", "op": "equals", "value": "urgente"},
                    "then": {"action": "set_text", "target": "pedido", "value": "URGENTE"},
                },
                {
                    "when": {"field": "estado", "op": "equals", "value": "urgente"},
                    "then": {"action": "move", "target": "pedido", "left": 10, "top": 20},
                },
            ],
        }
        context = {"estado": "urgente"}

        effective = apply_design_rules(design, context, None)

        self.assertEqual(effective["pedido"]["text"], "URGENTE")
        self.assertEqual(effective["pedido"]["left"], 10)
        self.assertEqual(effective["pedido"]["top"], 20)

    def test_rule_with_unknown_target_is_ignored_silently(self):
        from .rendering import apply_design_rules

        design = {
            "qr": {"left": 68, "top": 5},
            "rules": [
                {"when": {"field": "x", "op": "empty"}, "then": {"action": "hide", "target": "no_existe"}}
            ],
        }

        effective = apply_design_rules(design, {}, None)

        self.assertIn("qr", effective)


class LabelBatchComputedContextTests(APITestCase):
    """Historia 29 en el lote: un valor de {{bulto}}/{{bultos}} distinto
    por ítem, calculado sobre la posición real dentro del LOTE."""

    def setUp(self):
        subscriber_group, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(
            username="lotebultos@example.com",
            email="lotebultos@example.com",
            password="Clave123!",
        )
        self.user.groups.add(subscriber_group)

        self.orders = []
        for i in range(3):
            address = Address.objects.create(
                user=self.user, street=f"Calle {i}", number=str(i), city="CABA", is_default=(i == 0)
            )
            self.orders.append(Order.objects.create(user=self.user, address=address))

        self.template = LabelTemplate.objects.create(
            name="Plantilla lote bultos",
            owner=self.user,
            is_public=True,
            width_cm=10,
            height_cm=15,
            design=VALID_DESIGN,
        )

    def test_batch_of_three_resolves_bulto_de_bultos_per_item(self):
        from unittest.mock import patch

        from . import batch_views

        seen = []
        original_draw = batch_views.draw_label_page

        def spy_draw(pdf, design, width_cm, height_cm, context=None, logo_file=None, computed=None):
            seen.append(computed("bulto_de_bultos") if computed else None)
            return original_draw(
                pdf, design, width_cm, height_cm, context=context, logo_file=logo_file, computed=computed
            )

        with patch.object(batch_views, "draw_label_page", side_effect=spy_draw):
            file_bytes, item_count, skipped = batch_views._render_combined_pdf(
                self.orders, self.template, owner=self.user
            )

        self.assertEqual(item_count, 3)
        self.assertEqual(skipped, [])
        self.assertEqual(seen, ["1 de 3", "2 de 3", "3 de 3"])
