"""Los archivos con datos de compradores (lotes de rótulos, rótulos que pide
la tienda, fotos de rótulos) no se pueden adivinar ni se sirven en /media/
sin sesión."""

import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.documents.models import Document, UploadedLabelFile
from apps.labels.models import LabelTemplate
from apps.orders.models import Address, Order

User = get_user_model()
DESIGN = {"destinatario": {"left": 6, "top": 38}}


def bearer(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(user).access_token}"}


class BatchFileNameTests(APITestCase):
    def test_batch_file_is_not_guessable_but_downloads_with_a_clean_name(self):
        user = User.objects.create_user(username="u@example.com", email="u@example.com", password="Clave123!")
        address = Address.objects.create(user=user, recipient_name="Ana", street="Calle", number="1", city="CABA")
        order = Order.objects.create(user=user, address=address)
        template = LabelTemplate.objects.create(owner=user, name="P", width_cm=10, height_cm=15, design=DESIGN)
        response = self.client.post(
            "/api/v1/labels/batch/", {"order_ids": [order.id], "template_id": template.id}, format="json", **bearer(user)
        )
        self.assertIn(response.status_code, (200, 201, 202), response.data)
        document = Document.objects.get()
        stored = document.file.name.rsplit("/", 1)[-1]
        self.assertRegex(stored, rf"^rotulos-{document.pk}-[0-9a-f]{{16}}\.pdf$")

        listed = self.client.get("/api/v1/documents/", **bearer(user)).data["results"][0]
        self.assertEqual(listed["file_name"], f"rotulos-{document.pk}.pdf")
        download = self.client.get(f"/api/v1/documents/{document.pk}/download/", **bearer(user))
        self.assertIn(f'filename="rotulos-{document.pk}.pdf"', download["Content-Disposition"])


class UploadedPhotoNameTests(TestCase):
    def test_uploaded_photo_gets_a_random_name(self):
        user = User.objects.create_user(username="u@example.com", email="u@example.com", password="Clave123!")
        uploaded = UploadedLabelFile.objects.create(
            uploaded_by=user, size_bytes=72, file=SimpleUploadedFile("rotulo-de-juan-perez.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        )
        stored = uploaded.file.name.rsplit("/", 1)[-1]
        self.assertNotIn("juan", stored)
        self.assertRegex(stored, r"^[0-9a-f]{32}\.png$")


class CaddyMediaTests(SimpleTestCase):
    """En producción Caddy sirve /media/ directo: solo lo público."""

    def caddyfile(self):
        return (Path(settings.BASE_DIR).parent / "Caddyfile").read_text()

    def test_private_folders_are_not_served_directly(self):
        config = self.caddyfile()
        self.assertNotIn("handle /media/* {", config)
        for private in ("documents/", "store_labels/", "rotulos/"):
            self.assertNotRegex(config, rf"handle /media/{re.escape(private)}")

    def test_public_images_still_are(self):
        config = self.caddyfile()
        for public in ("store_logos", "accounts/logos", "labels/templates"):
            self.assertIn(f"/media/{public}/*", config)
