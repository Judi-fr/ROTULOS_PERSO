"""Galería de "+ Nueva plantilla" (fase 5): los ocho diseños sembrados por
``labels/0015``, en orden, válidos, imprimibles en PDF y ZPL y con códigos que
se leen a 203 dpi."""

import re
import unittest
from importlib import import_module

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework.test import APITestCase

from apps.accounts.tests.team_helpers import new_business

from ..gallery import GALLERY, STANDARD_TEMPLATE_NAME
from ..label_rendering import build_preview_context, render_label_pdf
from ..models import LabelTemplate
from ..serializers import validate_design
from ..zpl import render_label_zpl
from .test_scanning import decode, rasterize, zxingcpp

User = get_user_model()
EXPECTED_ORDER = ["Moderno", "Con la marca", "Destacado", "Editorial", "Banda lateral", "Postal", "Celdas",
                  STANDARD_TEMPLATE_NAME]
# Lo que puede ir en un rótulo (build_label_context + calculados): nada de
# productos, precios ni contacto del comprador.
ALLOWED_MARKERS = {
    "remitente", "remitente_domicilio", "remitente_telefono", "destinatario", "domicilio", "referencia",
    "localidad", "cp", "pais", "pedido", "fecha_pedido", "envio", "tracking", "tracking_url", "nombre", "peso",
    "bultos_pedido", "transportista", "fecha", "hora", "secuencia", "bulto", "bultos", "bulto_de_bultos",
}


def gallery_templates():
    return list(LabelTemplate.objects.filter(owner=None, is_public=True, gallery_order__isnull=False)
                .order_by("gallery_order"))


class GallerySeedTests(TestCase):
    def test_eight_in_the_users_order(self):
        self.assertEqual([t.name for t in gallery_templates()], EXPECTED_ORDER)

    def test_each_new_one_starts_with_version_one(self):
        for template in gallery_templates()[:-1]:
            with self.subTest(template=template.name):
                self.assertEqual(list(template.versions.values_list("number", flat=True)), [1])

    def test_migration_copy_matches_gallery_module(self):
        seeded = import_module("apps.labels.migrations.0015_gallery_templates").GALLERY
        self.assertEqual([item["design"] for item in seeded], [item["design"] for item in GALLERY])

    def test_designs_validate_and_use_only_label_markers(self):
        for item in GALLERY:
            with self.subTest(template=item["name"]):
                validate_design(item["design"])
                markers = set(re.findall(r"\{\{\s*([a-z_]+)", str(item["design"])))
                self.assertLessEqual(markers, ALLOWED_MARKERS)

    def test_every_data_field_is_placed(self):
        # El editor dibuja siempre los seis campos: si el diseño no los
        # ubica, al guardar aparecen donde quedaron por defecto.
        for item in GALLERY:
            with self.subTest(template=item["name"]):
                for field in ("remitente", "destinatario", "domicilio", "cp", "localidad", "pedido", "qr", "barcode"):
                    self.assertIn(field, item["design"])

    def test_pdf_and_zpl_draw_without_unreadable_codes(self):
        context = build_preview_context()
        for item in GALLERY:
            for name, render in (("pdf", lambda d, w: render_label_pdf(d, 10, 15, context=context, warnings=w)),
                                 ("zpl8", lambda d, w: render_label_zpl(d, 10, 15, context=context, warnings=w)),
                                 ("zpl12", lambda d, w: render_label_zpl(d, 10, 15, context=context, dpmm=12,
                                                                         warnings=w))):
                with self.subTest(template=item["name"], output=name):
                    warnings = []
                    render(item["design"], warnings)
                    codes = {warning["code"] for warning in warnings}
                    # Sin logo cargado, la imagen "logo" avisa; nada más.
                    self.assertLessEqual(codes, {"image_missing"})

    @unittest.skipIf(zxingcpp is None, "zxing-cpp no está instalado")
    def test_codes_read_back_at_203_dpi(self):
        context = build_preview_context()
        for item in GALLERY:
            with self.subTest(template=item["name"]):
                found = decode(rasterize(render_label_pdf(item["design"], 10, 15, context=context)))
                self.assertIn(("Code128", context["tracking"]), found)
                self.assertIn(("QRCode", context["tracking_url"]), found)


class GalleryApiTests(APITestCase):
    def setUp(self):
        user = User.objects.create_user(username="g@example.com", email="g@example.com", password="Clave123!")
        group, _ = Group.objects.get_or_create(name="subscriber")
        user.groups.add(group)
        self.data_user = new_business(user)
        self.client.force_authenticate(user=user)

    def test_gallery_filter_lists_only_the_gallery_in_order(self):
        LabelTemplate.objects.create(name="Mía", owner=self.data_user, width_cm=10, height_cm=15, design={})
        response = self.client.get("/api/v1/labels/templates/", {"gallery": "1"})
        self.assertEqual(response.status_code, 200)
        rows = response.data["results"] if isinstance(response.data, dict) else response.data
        self.assertEqual([row["name"] for row in rows], EXPECTED_ORDER)
        self.assertEqual([row["gallery_order"] for row in rows], list(range(1, 9)))

    def test_without_filter_own_templates_still_listed(self):
        LabelTemplate.objects.create(name="Mía", owner=self.data_user, width_cm=10, height_cm=15, design={})
        response = self.client.get("/api/v1/labels/templates/")
        rows = response.data["results"] if isinstance(response.data, dict) else response.data
        self.assertIn("Mía", [row["name"] for row in rows])

    def test_gallery_order_is_read_only(self):
        response = self.client.post("/api/v1/labels/templates/", {
            "name": "Colada", "width_cm": 10, "height_cm": 15, "design": {}, "gallery_order": 1,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(LabelTemplate.objects.get(pk=response.data["id"]).gallery_order)
