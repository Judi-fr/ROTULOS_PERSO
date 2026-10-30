"""Foto → plantilla del editor (2026-10-30, plan
docs/superpowers/plans/2026-10-30-foto-a-plantilla.md): lo que Claude leyó de
un rótulo se traduce al ``design`` de ``LabelTemplate`` para abrirlo en
editor_rotulos.html, en vez de quedar solo como diseño por elementos (que
ningún flujo de impresión usa)."""

from rest_framework.test import APITestCase

from apps.labels.models import LayoutVariable
from apps.processing.schema import build_schema


class CatalogTests(APITestCase):
    def test_catalog_has_barcode_and_tracking(self):
        for code, data_type in (("codigo_barras", "codigo_barras"), ("numero_seguimiento", "texto")):
            variable = LayoutVariable.objects.get(code=code)
            self.assertTrue(variable.is_active)
            self.assertTrue(variable.is_system)
            self.assertEqual(variable.data_type, data_type)
            self.assertTrue(variable.description.strip())
        schema = str(build_schema(LayoutVariable.objects.filter(is_active=True)))
        self.assertIn("codigo_barras", schema)
        self.assertIn("numero_seguimiento", schema)


def element(kind, x, y, w, h, variable=None, content="", **style):
    return {
        "element_type": kind, "variable": variable, "content": content,
        "x_mm": x, "y_mm": y, "width_mm": w, "height_mm": h, "style": style, "order": 10,
    }


def proposal(*elements, width=100, height=150):
    return {"name": "Rótulo importado de foto.jpg", "width_mm": width, "height_mm": height, "elements": list(elements)}


class TranslatorTests(APITestCase):
    def translate(self, *elements, **size):
        from apps.processing.to_template import proposal_to_template

        return proposal_to_template(proposal(*elements, **size))

    def test_text_variables_map_to_fields(self):
        result = self.translate(
            element("variable", 10, 30, 80, 8, "destinatario"),
            element("variable", 10, 45, 20, 6, "codigo_postal"),
            element("variable", 10, 60, 50, 6, "localidad_provincia"),
            element("variable", 10, 75, 30, 6, "numero_pedido"),
        )
        design = result["design"]
        self.assertEqual(design["destinatario"]["left"], 10.0)
        self.assertEqual(design["destinatario"]["top"], 20.0)
        self.assertEqual(design["destinatario"]["width"], 80.0)
        self.assertIn("cp", design)
        self.assertIn("localidad", design)
        self.assertIn("pedido", design)
        # Solo el dato: las etiquetas ("CP:", "DESTINATARIO") vienen de la foto
        # como textos fijos, y el texto por defecto del editor las repetía.
        self.assertEqual(design["destinatario"]["text"], "{{destinatario}}")
        self.assertEqual(design["cp"]["text"], "{{cp}}")
        self.assertEqual(result["name"], "Rótulo importado de foto.jpg")
        self.assertEqual((result["width_cm"], result["height_cm"]), (10.0, 15.0))

    def test_style_is_translated(self):
        design = self.translate(
            element("variable", 0, 0, 100, 8, "remitente", tamano_pt=14, negrita=True, alineacion="centro")
        )["design"]
        self.assertEqual(design["remitente"]["font_size"], 14)
        self.assertIs(design["remitente"]["bold"], True)
        self.assertEqual(design["remitente"]["align"], "center")
        self.assertNotIn("width", design["remitente"])

    def test_static_text_and_tracking_go_to_texts(self):
        texts = self.translate(
            element("texto_estatico", 5, 15, 10, 5, content="CP:"),
            element("variable", 5, 120, 60, 5, "numero_seguimiento"),
        )["design"]["texts"]
        self.assertEqual([entry["text"] for entry in texts], ["CP:", "{{tracking}}"])
        self.assertEqual(texts[0]["left"], 5.0)
        self.assertEqual(texts[0]["top"], 10.0)

    def test_codes_and_logo(self):
        design = self.translate(
            element("variable", 60, 5, 30, 30, "qr"),
            element("variable", 10, 120, 60, 15, "codigo_barras"),
            element("variable", 5, 5, 25, 15, "logo_empresa"),
        )["design"]
        self.assertEqual(design["qr"]["size"], 3.0)
        self.assertEqual((design["barcode"]["width"], design["barcode"]["height"]), (6.0, 1.5))
        self.assertEqual((design["logo"]["left"], design["logo"]["top"]), (5.0, round(5 / 150 * 100, 1)))

    def test_lines_and_boxes_become_shapes(self):
        shapes = self.translate(
            element("linea", 0, 75, 100, 0.3),
            element("linea", 50, 0, 0.3, 60),
            element("recuadro", 5, 90, 90, 30),
        )["design"]["shapes"]
        self.assertEqual([shape["kind"] for shape in shapes], ["line", "line", "rect"])
        self.assertEqual(shapes[0]["height"], 0)
        self.assertEqual(shapes[1]["width"], 0)
        self.assertEqual(shapes[2]["stroke"], "#000000")
        self.assertIsNone(shapes[2]["fill"])
        self.assertEqual(len({shape["id"] for shape in shapes}), 3)

    def test_duplicates_and_unknown_are_reported(self):
        result = self.translate(
            element("variable", 10, 30, 80, 8, "destinatario"),
            element("variable", 10, 50, 80, 8, "destinatario"),
            element("variable", 10, 70, 80, 8, "cuit_cliente"),
        )
        self.assertEqual(result["design"]["destinatario"]["top"], 20.0)
        self.assertEqual(len(result["omitted"]), 2)
        self.assertTrue(all(item["reason"] for item in result["omitted"]))

    def test_out_of_bounds_and_sizes_are_clamped(self):
        result = self.translate(element("variable", 95, 10, 30, 5, "domicilio"), width=350, height=20)
        entry = result["design"]["domicilio"]
        self.assertLessEqual(entry["left"], 100)
        self.assertLessEqual(entry["left"] + entry.get("width", 0), 100)
        self.assertEqual((result["width_cm"], result["height_cm"]), (30.0, 5.0))

    def test_result_validates_and_renders(self):
        from apps.labels.label_rendering import SAMPLE_LABEL_CONTEXT, render_label_pdf
        from apps.labels.serializers import validate_design

        result = self.translate(
            element("variable", 5, 5, 25, 15, "logo_empresa"),
            element("variable", 35, 8, 60, 6, "remitente", tamano_pt=9),
            element("linea", 0, 28, 100, 0.3),
            element("texto_estatico", 5, 32, 30, 5, content="DESTINATARIO", negrita=True),
            element("variable", 5, 40, 90, 8, "destinatario", tamano_pt=16, negrita=True),
            element("variable", 5, 52, 90, 6, "domicilio"),
            element("variable", 5, 60, 20, 6, "codigo_postal"),
            element("variable", 30, 60, 65, 6, "localidad_provincia"),
            element("variable", 65, 95, 30, 30, "qr"),
            element("variable", 5, 128, 60, 15, "codigo_barras"),
            element("variable", 5, 144, 60, 4, "numero_seguimiento"),
            element("recuadro", 2, 2, 96, 146),
        )
        design = validate_design(result["design"])
        pdf = render_label_pdf(design, result["width_cm"], result["height_cm"], context=SAMPLE_LABEL_CONTEXT)
        self.assertTrue(pdf.startswith(b"%PDF"))


class TemplateEndpointTests(APITestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from apps.accounts.tests.team_helpers import new_business
        from apps.documents.models import UploadedLabelFile
        from apps.processing.models import LabelImport

        self.user = User.objects.create_user(username="ana", email="ana@test.com", password="x")
        new_business(self.user)
        self.other = User.objects.create_user(username="beto", email="beto@test.com", password="x")
        new_business(self.other)
        upload = UploadedLabelFile.objects.create(
            file="rotulos/r.png", original_filename="r.png", mime_type="image/png", size_bytes=100, uploaded_by=self.user
        )
        self.with_proposal = LabelImport.objects.create(
            uploaded_file=upload, created_by=self.user,
            proposal=proposal(element("variable", 10, 30, 80, 8, "destinatario")),
        )
        self.without_proposal = LabelImport.objects.create(uploaded_file=upload, created_by=self.user)

    def url(self, label_import):
        return f"/api/v1/processing/label-imports/{label_import.pk}/template/"

    def test_endpoint_returns_template(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(self.url(self.with_proposal))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["design"]["destinatario"]["top"], 20.0)
        self.assertEqual(response.data["width_cm"], 10.0)
        self.assertEqual(response.data["omitted"], [])

    def test_endpoint_404_for_someone_else(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url(self.with_proposal)).status_code, 404)

    def test_endpoint_400_without_proposal(self):
        self.client.force_authenticate(self.user)
        response = self.client.get(self.url(self.without_proposal))
        self.assertEqual(response.status_code, 400)
        self.assertIn("todavía no tiene una lectura", response.data["detail"])
