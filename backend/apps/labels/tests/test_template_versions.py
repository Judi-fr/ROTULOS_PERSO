"""Versionado de plantillas (T#254): historial al crear/editar/duplicar,
versiones solo cuando cambia lo que se dibuja, vista previa y restauración."""

import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog

from ..models import LabelTemplate, LabelTemplateVersion
from .test_labels import VALID_DESIGN, auth_headers_for

User = get_user_model()
MOVED_DESIGN = {**VALID_DESIGN, "cp": {"left": 50, "top": 56, "text": "CP: 1000"}}


class TemplateVersionTests(APITestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        media_override = override_settings(MEDIA_ROOT=media)
        media_override.enable()
        self.addCleanup(media_override.disable)

        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(username="versiones@example.com", email="versiones@example.com", password="Clave123!")
        self.user.groups.add(subscriber)
        self.other = User.objects.create_user(username="ajeno@example.com", email="ajeno@example.com", password="Clave123!")
        self.other.groups.add(subscriber)
        self.headers = auth_headers_for(self.user)

    def url(self, template_id, suffix=""):
        return f"/api/v1/labels/templates/{template_id}/{suffix}"

    def create(self, **extra):
        payload = {"name": "Envíos", "width_cm": "10", "height_cm": "15", "design": VALID_DESIGN, **extra}
        response = self.client.post("/api/v1/labels/templates/", payload, format="json", **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def patch(self, template_id, data, headers=None):
        return self.client.patch(self.url(template_id), data, format="json", **(headers or self.headers))

    def test_create_starts_history_at_version_one(self):
        data = self.create(version_note="primera")
        self.assertEqual(data["current_version"], 1)
        self.assertTrue(data["can_edit"])
        self.assertNotIn("version_note", data)
        version = LabelTemplateVersion.objects.get(template_id=data["id"])
        self.assertEqual((version.number, version.note, version.created_by), (1, "primera", self.user))
        self.assertEqual(version.design, VALID_DESIGN)

    def test_only_drawing_changes_create_versions(self):
        template_id = self.create()["id"]
        response = self.patch(template_id, {"description": "otra"})
        self.assertEqual(response.data["current_version"], 1)

        response = self.patch(template_id, {"design": MOVED_DESIGN, "version_note": "moví el CP"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["current_version"], 2)

        # Guardar lo mismo otra vez no deja una versión vacía.
        self.assertEqual(self.patch(template_id, {"design": MOVED_DESIGN}).data["current_version"], 2)

        history = self.client.get(self.url(template_id, "versions/"), **self.headers).data
        self.assertEqual([v["number"] for v in history], [2, 1])
        self.assertEqual(history[0]["note"], "moví el CP")
        self.assertEqual(history[0]["created_by_email"], "versiones@example.com")
        self.assertEqual(history[1]["design"], VALID_DESIGN)

    def test_list_reports_current_version(self):
        template_id = self.create()["id"]
        self.patch(template_id, {"width_cm": "12"})
        listed = self.client.get("/api/v1/labels/templates/", **self.headers).data["results"]
        self.assertEqual(next(t for t in listed if t["id"] == template_id)["current_version"], 2)

    def test_restore_creates_a_new_version_with_the_old_drawing(self):
        template_id = self.create()["id"]
        self.patch(template_id, {"design": MOVED_DESIGN, "name": "Envíos nuevos"})

        response = self.client.post(self.url(template_id, "versions/1/restore/"), **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["current_version"], 3)
        self.assertEqual(response.data["design"], VALID_DESIGN)
        self.assertEqual(response.data["name"], "Envíos")

        restored = LabelTemplateVersion.objects.get(template_id=template_id, number=3)
        self.assertEqual(restored.restored_from, 1)
        self.assertEqual(LabelTemplateVersion.objects.filter(template_id=template_id).count(), 3)
        self.assertTrue(LabelTemplate.objects.get(pk=template_id).preview)
        log = AuditLog.objects.filter(action="template.update", target_id=str(template_id)).latest("created_at")
        self.assertEqual(log.changes["version"], {"from": 2, "to": "restaurada la 1"})

    def test_restore_unknown_version_is_404(self):
        template_id = self.create()["id"]
        response = self.client.post(self.url(template_id, "versions/9/restore/"), **self.headers)
        self.assertEqual(response.status_code, 404)

    def test_someone_else_cannot_restore_a_public_template(self):
        public = LabelTemplate.objects.create(name="Pública", is_public=True, width_cm=10, height_cm=15, design=VALID_DESIGN)
        public.versions.create(number=1, name="Pública", width_cm=10, height_cm=15, design=VALID_DESIGN)
        headers = auth_headers_for(self.other)

        listed = self.client.get(self.url(public.pk), **headers).data
        self.assertFalse(listed["can_edit"])
        self.assertEqual(self.client.get(self.url(public.pk, "versions/"), **headers).status_code, 200)
        response = self.client.post(self.url(public.pk, "versions/1/restore/"), **headers)
        self.assertEqual(response.status_code, 403)

    def test_version_preview_is_the_pdf_of_that_version(self):
        template_id = self.create()["id"]
        self.patch(template_id, {"width_cm": "20"})
        response = self.client.get(self.url(template_id, "versions/1/preview/"), **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn('plantilla-%d-v1.pdf' % template_id, response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_duplicate_starts_its_own_history(self):
        template_id = self.create()["id"]
        self.patch(template_id, {"design": MOVED_DESIGN})
        response = self.client.post(self.url(template_id, "duplicate/"), **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        copy_versions = LabelTemplateVersion.objects.filter(template_id=response.data["id"])
        self.assertEqual(copy_versions.count(), 1)
        self.assertEqual(copy_versions.get().note, "Copia de «Envíos» (versión 2)")
        self.assertEqual(copy_versions.get().design, MOVED_DESIGN)
