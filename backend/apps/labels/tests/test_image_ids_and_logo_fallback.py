"""Fix round de la tarea 5: ``GET /labels/images/?ids=`` (el editor dibuja lo
que el PDF imprime) y el logo del comercio como respaldo del logo de la
plantilla en la vista previa y la miniatura."""

import shutil
import tempfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APITestCase

from apps.accounts.models import AccountSettings
from apps.accounts.tests.team_helpers import new_business

from ..models import LabelImage, LabelTemplate
from ..thumbnails import render_template_thumbnail
from .test_labels import auth_headers_for

User = get_user_model()
URL = "/api/v1/labels/images/"


def png(size=(10, 10)):
    output = BytesIO()
    Image.new("RGB", size, (20, 120, 20)).save(output, format="PNG")
    return output.getvalue()


def design(image):
    return {"shapes": [{"id": "im", "kind": "image", "image": image, "left": 0, "top": 0, "width": 30, "height": 20}]}


class MediaMixin:
    def use_media(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)


class ImageIdsTests(MediaMixin, APITestCase):
    def setUp(self):
        self.use_media()
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(username="a@example.com", email="a@example.com", password="Clave123!")
        self.user.groups.add(subscriber)
        self.biz = new_business(self.user)
        other = User.objects.create_user(username="b@example.com", email="b@example.com", password="Clave123!")
        other.groups.add(subscriber)
        self.other_biz = new_business(other)
        make = lambda owner, name, **kw: LabelImage.objects.create(  # noqa: E731
            owner=owner, image=ContentFile(png(), name=name), **kw)
        self.active = make(self.biz, "a.png")
        self.inactive = make(self.biz, "i.png", is_active=False)
        self.theirs = make(self.other_biz, "t.png")
        self.public_img = make(self.other_biz, "p.png")
        self.private_img = make(self.other_biz, "q.png")
        LabelTemplate.objects.create(name="Pub", owner=self.other_biz, is_public=True, width_cm=10, height_cm=15,
                                     design=design(self.public_img.pk))
        self.private_tpl = LabelTemplate.objects.create(
            name="Priv", owner=self.other_biz, is_public=False, width_cm=10, height_cm=15,
            design=design(self.private_img.pk))
        self.mine_tpl = LabelTemplate.objects.create(
            name="Mía", owner=self.biz, width_cm=10, height_cm=15, design=design(self.theirs.pk))
        self.headers = auth_headers_for(self.user)

    def ids(self, query):
        response = self.client.get(URL + query, **self.headers)
        self.assertEqual(response.status_code, 200, response.data)
        return [row["id"] for row in response.data]

    def test_without_ids_lists_only_active_own(self):
        self.assertEqual(self.ids(""), [self.active.pk])

    def test_inactive_own_image_is_returned(self):
        self.assertEqual(self.ids(f"?ids={self.inactive.pk}"), [self.inactive.pk])

    def test_row_has_the_list_fields(self):
        row = self.client.get(f"{URL}?ids={self.inactive.pk}", **self.headers).data[0]
        self.assertEqual(set(row), {"id", "url", "name", "width_px", "height_px", "created_at"})

    def test_foreign_id_is_omitted(self):
        self.assertEqual(self.ids(f"?ids={self.theirs.pk},{self.inactive.pk}"), [self.inactive.pk])

    def test_public_template_image_is_returned(self):
        self.assertEqual(self.ids(f"?ids={self.public_img.pk}"), [self.public_img.pk])

    def test_template_param_adds_its_ids(self):
        self.assertEqual(self.ids(f"?ids={self.theirs.pk}&template={self.mine_tpl.pk}"), [self.theirs.pk])

    def test_unreadable_template_adds_nothing(self):
        self.assertEqual(self.ids(f"?ids={self.private_img.pk}&template={self.private_tpl.pk}"), [])

    def test_garbage_ids_are_ignored(self):
        self.assertEqual(self.ids(f"?ids=x,,{self.inactive.pk}"), [self.inactive.pk])


class LogoFallbackTests(MediaMixin, APITestCase):
    def setUp(self):
        self.use_media()
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(username="c@example.com", email="c@example.com", password="Clave123!")
        self.user.groups.add(subscriber)
        self.biz = new_business(self.user)
        self.headers = auth_headers_for(self.user)
        self.design = design("logo")

    def give_account_logo(self):
        account, _ = AccountSettings.objects.get_or_create(user=self.biz)
        account.logo.save("logo.png", ContentFile(png((40, 20))), save=True)

    def preview(self, **extra):
        return self.client.post("/api/v1/labels/preview/", {"design": self.design, **extra}, format="json",
                                **self.headers)

    def test_preview_uses_the_account_logo(self):
        self.give_account_logo()
        response = self.preview()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("image_missing", response.get("X-Label-Warnings", ""))

    def test_preview_without_any_logo_still_warns(self):
        response = self.preview()
        self.assertIn("image_missing", response["X-Label-Warnings"])

    def test_template_preview_action_and_thumbnail_use_the_account_logo(self):
        template = LabelTemplate.objects.create(name="T", owner=self.biz, width_cm=10, height_cm=15, design=self.design)
        before = render_template_thumbnail(template)
        self.give_account_logo()
        self.assertFalse(render_template_thumbnail(template) == before)
        response = self.client.get(f"/api/v1/labels/templates/{template.pk}/preview/", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.content == self._preview_without_logo(template))

    def _preview_without_logo(self, template):
        AccountSettings.objects.filter(user=self.biz).update(logo="")
        return self.client.get(f"/api/v1/labels/templates/{template.pk}/preview/", **self.headers).content
