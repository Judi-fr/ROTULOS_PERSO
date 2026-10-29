"""Biblioteca de imágenes del comercio (``LabelImage``): subida normalizada,
listado, borrado lógico y límites."""

import base64
import shutil
import tempfile
from io import BytesIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from PIL import Image
from rest_framework.test import APITestCase

from apps.accounts.tests.team_helpers import new_business
from apps.accounts.tests.test_team_permissions import add_employee

from .. import label_images
from ..models import LabelImage
from .test_labels import auth_headers_for

User = get_user_model()
URL = "/api/v1/labels/images/"


def image_bytes(size=(40, 20), fmt="PNG", mode="RGB", **save):
    output = BytesIO()
    Image.new(mode, size, (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30)).save(output, format=fmt, **save)
    return output.getvalue()


def noisy_png():
    import os

    output = BytesIO()
    Image.frombytes("RGB", (60, 60), os.urandom(60 * 60 * 3)).save(output, format="PNG")
    return output.getvalue()


def upload(data, name="foto.png", content_type="image/png"):
    return SimpleUploadedFile(name, data, content_type=content_type)


class LabelImageTests(APITestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)

        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(username="img@example.com", email="img@example.com", password="Clave123!")
        self.user.groups.add(subscriber)
        self.data_user = new_business(self.user)
        self.other = User.objects.create_user(username="otro@example.com", email="otro@example.com", password="Clave123!")
        self.other.groups.add(subscriber)
        new_business(self.other)
        self.headers = auth_headers_for(self.user)

    def post_file(self, data, **kw):
        return self.client.post(URL, {"image": upload(data, **kw)}, format="multipart", **self.headers)

    def test_upload_multipart_and_data_url(self):
        response = self.post_file(image_bytes())
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(set(response.data), {"id", "url", "name", "width_px", "height_px", "created_at"})
        row = LabelImage.objects.get(pk=response.data["id"])
        self.assertEqual(row.owner, self.data_user)
        self.assertEqual(row.created_by, self.user)
        self.assertRegex(row.image.name, r"^labels/images/[0-9a-f]{32}\.png$")
        self.assertTrue(Path(row.image.path).exists())

        data_url = "data:image/png;base64," + base64.b64encode(image_bytes()).decode()
        response = self.client.post(URL, {"image": data_url, "name": "Mi logo"}, format="json", **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["name"], "Mi logo")
        self.assertEqual(LabelImage.objects.get(pk=response.data["id"]).owner, self.data_user)

    def test_exif_rotation_applied_and_stripped(self):
        source = Image.new("RGB", (40, 20), (10, 200, 10))
        exif = Image.Exif()
        exif[0x0112] = 6
        output = BytesIO()
        source.save(output, format="JPEG", exif=exif)
        response = self.post_file(output.getvalue(), name="f.jpg", content_type="image/jpeg")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data["width_px"], response.data["height_px"]), (20, 40))
        row = LabelImage.objects.get(pk=response.data["id"])
        self.assertTrue(row.image.name.endswith(".jpg"))
        saved = Image.open(row.image.path)
        self.assertEqual(saved.size, (20, 40))
        self.assertEqual(len(saved.getexif()), 0)

    def test_big_image_downscaled(self):
        response = self.post_file(image_bytes((3000, 1000)))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual((response.data["width_px"], response.data["height_px"]), (2000, 667))
        row = LabelImage.objects.get(pk=response.data["id"])
        self.assertEqual(Image.open(row.image.path).size, (2000, 667))

    def test_jpeg_source_without_alpha_stored_as_jpg(self):
        response = self.post_file(image_bytes(fmt="JPEG"), name="f.jpg", content_type="image/jpeg")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(LabelImage.objects.get(pk=response.data["id"]).image.name.endswith(".jpg"))

    def test_gif_source_kept_lossless_as_png(self):
        response = self.post_file(image_bytes(fmt="GIF"), name="f.gif", content_type="image/gif")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(LabelImage.objects.get(pk=response.data["id"]).image.name.endswith(".png"))

    def test_huge_dimensions_rejected_not_500(self):
        output = BytesIO()
        Image.new("1", (20000, 20000)).save(output, format="PNG")
        data = output.getvalue()
        response = self.post_file(data)
        self.assertEqual(response.status_code, 400)
        data_url = "data:image/png;base64," + base64.b64encode(data).decode()
        response = self.client.post(URL, {"image": data_url}, format="json", **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(LabelImage.objects.count(), 0)

    def test_over_pixel_cap_gets_clear_message(self):
        output = BytesIO()
        Image.new("1", (7000, 7000)).save(output, format="PNG")  # 49 MP: bajo el límite de Pillow, sobre el nuestro
        response = self.post_file(output.getvalue())
        self.assertEqual(response.status_code, 400)
        self.assertIn("demasiado grande", str(response.data))
        data_url = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
        response = self.client.post(URL, {"image": data_url}, format="json", **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("demasiado grande", str(response.data))

    def test_data_url_size_limit_uses_the_constant(self):
        data_url = "data:image/png;base64," + base64.b64encode(noisy_png()).decode()
        with mock.patch.object(label_images, "MAX_UPLOAD_BYTES", 1000):
            response = self.client.post(URL, {"image": data_url}, format="json", **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("tamaño", str(response.data))

    def test_png_alpha_kept_as_png(self):
        response = self.post_file(image_bytes(mode="RGBA"))
        self.assertEqual(response.status_code, 201, response.data)
        row = LabelImage.objects.get(pk=response.data["id"])
        self.assertTrue(row.image.name.endswith(".png"))
        saved = Image.open(row.image.path)
        self.assertIn("A", saved.getbands())
        self.assertEqual(saved.convert("RGBA").getpixel((0, 0))[3], 128)

    def test_rejects_non_image_and_too_big(self):
        response = self.post_file(b"esto no es una imagen", name="x.png")
        self.assertEqual(response.status_code, 400)
        self.assertIn("imagen", str(response.data).lower())
        with mock.patch.object(label_images, "MAX_UPLOAD_BYTES", 1000):
            response = self.post_file(noisy_png())
        self.assertEqual(response.status_code, 400)
        self.assertIn("tamaño", str(response.data))
        data_url = "data:image/png;base64," + base64.b64encode(b"nada").decode()
        response = self.client.post(URL, {"image": data_url}, format="json", **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(LabelImage.objects.count(), 0)

    def test_list_only_own_active(self):
        first = self.post_file(image_bytes()).data
        second = self.post_file(image_bytes()).data
        other = self.client.post(URL, {"image": upload(image_bytes())}, format="multipart", **auth_headers_for(self.other))
        self.assertEqual(other.status_code, 201)
        response = self.client.get(URL, **self.headers)
        self.assertEqual([row["id"] for row in response.data], [second["id"], first["id"]])
        self.client.delete(f"{URL}{first['id']}/", **self.headers)
        response = self.client.get(URL, **self.headers)
        self.assertEqual([row["id"] for row in response.data], [second["id"]])
        # No se puede borrar la de otro comercio.
        self.assertEqual(self.client.delete(f"{URL}{other.data['id']}/", **self.headers).status_code, 404)

    def test_delete_is_soft(self):
        created = self.post_file(image_bytes()).data
        response = self.client.delete(f"{URL}{created['id']}/", **self.headers)
        self.assertEqual(response.status_code, 204)
        row = LabelImage.objects.get(pk=created["id"])
        self.assertFalse(row.is_active)
        self.assertTrue(Path(row.image.path).exists())
        self.assertIsNotNone(label_images.image_file(row.pk))
        self.assertIsNone(label_images.image_file(row.pk + 999))

    def test_limit_per_business(self):
        with mock.patch.object(label_images, "MAX_IMAGES_PER_OWNER", 2):
            self.assertEqual(self.post_file(image_bytes()).status_code, 201)
            self.assertEqual(self.post_file(image_bytes()).status_code, 201)
            response = self.post_file(image_bytes())
            self.assertEqual(response.status_code, 400)
            self.assertIn("2", str(response.data))

    def test_permissions(self):
        employee, _ = add_employee(self.user, "emp@example.com", accesses=["orders.view"])
        headers = auth_headers_for(employee)
        self.assertEqual(self.client.get(URL, **headers).status_code, 403)
        self.assertEqual(
            self.client.post(URL, {"image": upload(image_bytes())}, format="multipart", **headers).status_code, 403
        )
        created = self.post_file(image_bytes()).data
        self.assertEqual(self.client.delete(f"{URL}{created['id']}/", **headers).status_code, 403)


class CaddyLabelImagesTests(SimpleTestCase):
    def test_caddyfile_serves_label_images_publicly(self):
        config = (Path(settings.BASE_DIR).parent / "Caddyfile").read_text()
        line = next(row for row in config.splitlines() if "@public_media" in row)
        self.assertIn("/media/labels/images/*", line)
