"""Miniatura PNG de las plantillas (``thumbnails.py``): se genera al crear,
editar el dibujo o duplicar, sale del mismo render que la vista previa y un
diseño que no se puede dibujar nunca impide guardar la plantilla."""

import shutil
import tempfile
from io import BytesIO, StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import override_settings
from PIL import Image
from rest_framework.test import APITestCase

from ..models import LabelTemplate
from ..thumbnails import THUMBNAIL_WIDTH_PX
from .test_labels import TINY_PNG_DATA_URL, VALID_DESIGN, auth_headers_for

User = get_user_model()


class TemplateThumbnailTests(APITestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        media_override = override_settings(MEDIA_ROOT=media)
        media_override.enable()
        self.addCleanup(media_override.disable)

        self.user = User.objects.create_user(
            username="miniaturas@example.com", email="miniaturas@example.com", password="Clave123!"
        )
        self.user.groups.add(Group.objects.get_or_create(name="subscriber")[0])
        self.headers = auth_headers_for(self.user)

    def create_template(self, **overrides):
        payload = {"name": "Mi plantilla", "width_cm": "10", "height_cm": "15", "design": VALID_DESIGN}
        payload.update(overrides)
        return self.client.post("/api/v1/labels/templates/", payload, format="json", **self.headers)

    def thumbnail_of(self, template_id):
        template = LabelTemplate.objects.get(pk=template_id)
        self.assertTrue(template.preview, "la plantilla quedó sin miniatura")
        with template.preview.open("rb") as handle:
            return Image.open(BytesIO(handle.read()))

    def test_create_generates_png_with_label_proportions(self):
        response = self.create_template()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["preview"])
        image = self.thumbnail_of(response.data["id"])
        self.assertEqual(image.format, "PNG")
        self.assertEqual(image.width, THUMBNAIL_WIDTH_PX)
        self.assertAlmostEqual(image.height / image.width, 15 / 10, places=2)

    def test_changing_the_size_redraws_it_and_renaming_does_not(self):
        template_id = self.create_template().data["id"]
        first = LabelTemplate.objects.get(pk=template_id).preview.name

        self.client.patch(f"/api/v1/labels/templates/{template_id}/", {"name": "Otro nombre"}, format="json", **self.headers)
        self.assertEqual(LabelTemplate.objects.get(pk=template_id).preview.name, first)

        response = self.client.patch(
            f"/api/v1/labels/templates/{template_id}/", {"width_cm": "15", "height_cm": "10"}, format="json", **self.headers
        )
        self.assertEqual(response.status_code, 200, response.data)
        image = self.thumbnail_of(template_id)
        self.assertAlmostEqual(image.height / image.width, 10 / 15, places=2)

    def test_an_image_sent_by_the_client_is_kept(self):
        response = self.create_template(preview=TINY_PNG_DATA_URL)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.thumbnail_of(response.data["id"]).size, (1, 1))

    def test_duplicate_of_a_template_without_thumbnail_gets_one(self):
        original = LabelTemplate.objects.create(name="Vieja", owner=self.user, width_cm=10, height_cm=15, design=VALID_DESIGN)
        response = self.client.post(f"/api/v1/labels/templates/{original.pk}/duplicate/", **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.thumbnail_of(response.data["id"]).width, THUMBNAIL_WIDTH_PX)

    def test_a_render_failure_still_saves_the_template(self):
        with patch("apps.labels.thumbnails.render_label_pdf", side_effect=RuntimeError("diseño roto")), self.assertLogs(
            "apps.labels.thumbnails", level="ERROR"
        ):
            response = self.create_template()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(LabelTemplate.objects.get(pk=response.data["id"]).preview)

    def test_command_fills_only_missing_thumbnails(self):
        missing = LabelTemplate.objects.create(name="Sin", owner=self.user, width_cm=10, height_cm=15, design=VALID_DESIGN)
        LabelTemplate.objects.create(name="Archivada", owner=self.user, width_cm=10, height_cm=15, design=VALID_DESIGN, is_active=False)
        # Las activas sin miniatura: la nueva y la del sistema que siembra la migración.
        expected = LabelTemplate.objects.filter(is_active=True, preview__in=["", None]).count()
        out = StringIO()
        call_command("generate_template_thumbnails", stdout=out)
        self.assertIn(f"Miniaturas generadas: {expected}. Fallidas: 0.", out.getvalue())
        self.assertFalse(LabelTemplate.objects.get(name="Archivada").preview)
        self.assertTrue(LabelTemplate.objects.get(pk=missing.pk).preview)

        out = StringIO()
        call_command("generate_template_thumbnails", stdout=out)
        self.assertIn("Miniaturas generadas: 0.", out.getvalue())
