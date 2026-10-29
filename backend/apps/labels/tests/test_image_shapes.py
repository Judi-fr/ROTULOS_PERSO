"""Forma imagen (fase 3): validación, preparación y dibujo en PDF."""

import shutil
import tempfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image
from rest_framework import serializers as drf_serializers
from rest_framework.test import APITestCase

from apps.accounts.tests.team_helpers import new_business

from .. import label_images
from ..design_shapes import validate_shapes
from ..label_rendering import CM_TO_POINTS, draw_label_page, render_label_pdf
from ..models import LabelImage
from .test_labels import auth_headers_for
from .test_shapes_pdf import RecordingCanvas

User = get_user_model()
WIDTH_PT, HEIGHT_PT = 10 * CM_TO_POINTS, 15 * CM_TO_POINTS


def png_bytes(size, color=(255, 0, 0, 255)):
    output = BytesIO()
    Image.new("RGBA", size, color).save(output, format="PNG")
    return output.getvalue()


def source(size=(400, 100)):
    return BytesIO(png_bytes(size))


class ValidationTests(SimpleTestCase):
    def check(self, **extra):
        shape = {"id": "im", "kind": "image", "left": 0, "top": 0, "width": 10, "height": 10, **extra}
        validate_shapes([shape], None)

    def test_accepts_id_and_logo(self):
        self.check(image=5)
        self.check(image="logo", fit="cover", circle=True)

    def test_rejects_bad_shapes(self):
        for extra in (
            {},  # sin image
            {"image": 0},
            {"image": True},
            {"image": "otra"},
            {"image": 5, "fit": "stretch"},
            {"image": 5, "circle": "si"},
            {"image": 5, "text": {"value": "x"}},
        ):
            with self.subTest(extra=extra), self.assertRaises(drf_serializers.ValidationError):
                self.check(**extra)

    def test_image_keys_only_on_images(self):
        with self.assertRaises(drf_serializers.ValidationError):
            validate_shapes([{"id": "r", "kind": "rect", "left": 0, "top": 0, "image": 3}], None)


class PrepareTests(SimpleTestCase):
    def test_contain_centres_with_transparent_bands(self):
        image = label_images.prepare(source(), 100, 100, "contain", False, 0)
        self.assertEqual(image.size, (100, 100))
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.getpixel((50, 50))[3], 255)
        self.assertEqual(image.getpixel((50, 30))[3], 0)
        self.assertEqual(image.getpixel((50, 70))[3], 0)
        band = [y for y in range(100) if image.getpixel((50, y))[3] == 255]
        self.assertEqual((len(band), band[0]), (25, 37))
        self.assertEqual(image.getpixel((0, 50))[3], 255)

    def test_cover_fills_everything(self):
        image = label_images.prepare(source(), 100, 100, "cover", False, 0)
        self.assertTrue(all(image.getpixel((x, y))[3] == 255 for x in (0, 50, 99) for y in (0, 50, 99)))

    def test_circle_clears_corners(self):
        image = label_images.prepare(source((100, 100)), 100, 100, "cover", True, 30)
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertEqual(image.getpixel((99, 99))[3], 0)
        self.assertEqual(image.getpixel((50, 50))[3], 255)

    def test_radius_rounds_box_corners_in_cover_and_contain(self):
        for fit in ("cover", "contain"):
            image = label_images.prepare(source((100, 100)), 100, 100, fit, False, 30)
            self.assertEqual(image.getpixel((0, 0))[3], 0, fit)
            self.assertEqual(image.getpixel((50, 50))[3], 255, fit)


class PdfTests(TestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        self.user = User.objects.create_user(username="p@example.com", email="p@example.com", password="Clave123!")
        self.row = LabelImage.objects.create(
            owner=self.user, name="x", image=ContentFile(png_bytes((40, 20)), name="a.png")
        )

    def draw(self, shape, **kwargs):
        pdf = RecordingCanvas()
        pdf.images = []
        pdf.drawImage = lambda reader, *args, **kw: pdf.images.append((args, kw))
        pdf.circle = lambda *args, **kw: pdf.calls.append(("circle", args, kw, None, pdf.stroke, None, None))
        warnings = []
        base = {"id": "im", "kind": "image", "left": 10, "top": 20, "width": 50, "height": 10}
        draw_label_page(pdf, {"shapes": [{**base, **shape}]}, 10, 15, context={}, warnings=warnings, **kwargs)
        return pdf, warnings

    def test_draws_once_in_the_right_box(self):
        pdf, warnings = self.draw({"image": self.row.pk})
        self.assertEqual(warnings, [])
        [(args, kw)] = pdf.images
        x, y, w, h = args
        self.assertAlmostEqual(x, WIDTH_PT * 0.10, places=3)
        self.assertAlmostEqual(y, HEIGHT_PT * 0.70, places=3)
        self.assertAlmostEqual(w, WIDTH_PT * 0.50, places=3)
        self.assertAlmostEqual(h, HEIGHT_PT * 0.10, places=3)
        self.assertEqual(kw, {"mask": "auto"})

    def test_stroke_follows_the_box_shape(self):
        pdf, _ = self.draw({"image": self.row.pk, "stroke": "#000000", "radius": 2})
        self.assertEqual(len(pdf.of("roundRect")), 1)
        pdf, _ = self.draw({"image": self.row.pk, "stroke": "#000000", "circle": True})
        self.assertEqual(len(pdf.of("circle")), 1)

    def test_missing_image_warns_and_draws_nothing(self):
        pdf, warnings = self.draw({"image": 99999})
        self.assertEqual(pdf.images, [])
        self.assertEqual([w["code"] for w in warnings], ["image_missing"])

    def test_logo_without_logo_file_warns(self):
        pdf, warnings = self.draw({"image": "logo"})
        self.assertEqual(pdf.images, [])
        self.assertEqual([w["code"] for w in warnings], ["image_missing"])

    def test_logo_uses_the_logo_file(self):
        pdf, warnings = self.draw({"image": "logo"}, logo_file=source((20, 20)))
        self.assertEqual((len(pdf.images), warnings), (1, []))

    def test_deleted_image_still_draws(self):
        self.row.is_active = False
        self.row.save()
        pdf, _ = self.draw({"image": self.row.pk})
        self.assertEqual(len(pdf.images), 1)

    def test_real_pdf_with_image(self):
        data = render_label_pdf(
            {"shapes": [{"id": "im", "kind": "image", "image": self.row.pk, "left": 0, "top": 0,
                         "width": 50, "height": 20}]}, 10, 15, context={},
        )
        self.assertTrue(data.startswith(b"%PDF"))

    def test_design_without_images_does_no_queries(self):
        design = {"shapes": [{"id": "r", "kind": "rect", "left": 0, "top": 0, "width": 10, "height": 10,
                              "fill": "#000000"}]}
        with self.assertNumQueries(0):
            render_label_pdf(design, 10, 15, context={})


class OwnershipTests(APITestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(username="o@example.com", email="o@example.com", password="Clave123!")
        self.user.groups.add(subscriber)
        self.biz = new_business(self.user)
        other = User.objects.create_user(username="x@example.com", email="x@example.com", password="Clave123!")
        other.groups.add(subscriber)
        other_biz = new_business(other)
        self.mine = LabelImage.objects.create(owner=self.biz, image=ContentFile(png_bytes((10, 10)), name="m.png"))
        self.theirs = LabelImage.objects.create(owner=other_biz, image=ContentFile(png_bytes((10, 10)), name="t.png"))

    def post(self, image_id):
        design = {"shapes": [{"id": "im", "kind": "image", "image": image_id, "left": 0, "top": 0,
                              "width": 10, "height": 10}]}
        return self.client.post(
            "/api/v1/labels/templates/",
            {"name": "T", "width_cm": "10", "height_cm": "15", "design": design},
            format="json", **auth_headers_for(self.user),
        )

    def test_other_business_image_is_refused_own_is_accepted(self):
        self.assertEqual(self.post(self.theirs.pk).status_code, 400)
        self.assertEqual(self.post(self.mine.pk).status_code, 201)

    def test_inactive_own_image_can_still_be_saved(self):
        self.mine.is_active = False
        self.mine.save()
        self.assertEqual(self.post(self.mine.pk).status_code, 201)

    def test_saved_label_serializer_checks_too(self):
        response = self.client.post(
            "/api/v1/labels/labels/",
            {"name": "L", "width_cm": "10", "height_cm": "15",
             "design": {"shapes": [{"id": "im", "kind": "image", "image": self.theirs.pk, "left": 0, "top": 0,
                                    "width": 10, "height": 10}]}},
            format="json", **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("design", response.data)


def image_design(image_id):
    return {"shapes": [{"id": "im", "kind": "image", "image": image_id, "left": 0, "top": 0,
                        "width": 10, "height": 10}]}


class SharedOwnershipRuleTests(APITestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(username="s@example.com", email="s@example.com", password="Clave123!")
        self.user.groups.add(subscriber)
        self.biz = new_business(self.user)
        other = User.objects.create_user(username="z@example.com", email="z@example.com", password="Clave123!")
        other.groups.add(subscriber)
        self.other_biz = new_business(other)
        make = lambda owner, name: LabelImage.objects.create(  # noqa: E731
            owner=owner, image=ContentFile(png_bytes((10, 10)), name=name))
        self.mine = make(self.biz, "m.png")
        self.theirs = make(self.other_biz, "t.png")
        self.public_img = make(self.other_biz, "p.png")
        from ..models import LabelTemplate

        self.public = LabelTemplate.objects.create(
            name="Pública", owner=self.other_biz, is_public=True, width_cm=10, height_cm=15,
            design=image_design(self.public_img.pk))
        self.headers = auth_headers_for(self.user)

    def preview(self, design, **extra):
        return self.client.post("/api/v1/labels/preview/", {"design": design, **extra}, format="json",
                                **self.headers)

    def test_preview_foreign_id_is_refused(self):
        self.assertEqual(self.preview(image_design(self.theirs.pk)).status_code, 400)

    def test_preview_own_id_is_ok(self):
        self.assertEqual(self.preview(image_design(self.mine.pk)).status_code, 200)

    def test_preview_id_from_a_public_template_is_ok(self):
        self.assertEqual(self.preview(image_design(self.public_img.pk)).status_code, 200)

    def test_preview_logo_needs_no_check(self):
        self.assertEqual(self.preview(image_design("logo")).status_code, 200)

    def test_duplicate_public_then_patch_unchanged(self):
        response = self.client.post(f"/api/v1/labels/templates/{self.public.pk}/duplicate/", **self.headers)
        self.assertEqual(response.status_code, 201, response.data)
        patched = self.client.patch(
            f"/api/v1/labels/templates/{response.data['id']}/",
            {"design": response.data["design"]}, format="json", **self.headers)
        self.assertEqual(patched.status_code, 200, patched.data)

    def test_restore_then_patch_unchanged(self):
        from ..models import LabelTemplate
        from ..template_versions import record_version

        copy = LabelTemplate.objects.create(
            name="C", owner=self.biz, width_cm=10, height_cm=15, design=image_design(self.public_img.pk))
        record_version(copy, user=self.user)
        copy.design = {}
        copy.save()
        record_version(copy, user=self.user)
        url = f"/api/v1/labels/templates/{copy.pk}/"
        self.assertEqual(self.client.post(url + "versions/1/restore/", **self.headers).status_code, 200)
        patched = self.client.patch(url, {"design": image_design(self.public_img.pk)}, format="json",
                                    **self.headers)
        self.assertEqual(patched.status_code, 200, patched.data)

    def test_admin_without_business_previews_design_without_images(self):
        admin = User.objects.create_user(username="ad@example.com", email="ad@example.com",
                                         password="Clave123!", is_staff=True)
        admin.groups.add(Group.objects.get_or_create(name="admin")[0])
        response = self.client.post("/api/v1/labels/preview/", {"design": {"shapes": []}}, format="json",
                                    **auth_headers_for(admin))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(label_images.allowed_image_ids(admin), {self.public_img.pk})


class ImageDrawDetailsTests(TestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)

    def draw(self, shape, **kwargs):
        pdf = RecordingCanvas()
        pdf.drawImage = lambda *a, **k: None
        pdf.circle = lambda *a, **k: pdf.calls.append(("circle", a, k))
        base = {"id": "im", "kind": "image", "left": 10, "top": 20, "width": 50, "height": 10}
        draw_label_page(pdf, {"shapes": [{**base, **shape}]}, 10, 15, context={}, **kwargs)
        return pdf

    def test_circle_stroke_centre_and_radius(self):
        pdf = self.draw({"image": "logo", "stroke": "#000000", "circle": True}, logo_file=source((20, 20)))
        [(_, args, _)] = pdf.of("circle")
        self.assertAlmostEqual(args[0], WIDTH_PT * 0.35, places=3)  # centro x de la caja
        self.assertAlmostEqual(args[1], HEIGHT_PT * 0.75, places=3)  # centro y
        self.assertAlmostEqual(args[2], HEIGHT_PT * 0.10 / 2, places=3)  # radio = lado menor / 2

    def test_cover_keeps_both_halves_in_a_wide_image(self):
        wide = Image.new("RGBA", (400, 100), (255, 0, 0, 255))
        wide.paste((0, 0, 255, 255), (200, 0, 400, 100))
        output = BytesIO()
        wide.save(output, format="PNG")
        output.seek(0)
        image = label_images.prepare(output, 100, 100, "cover", False, 0)
        self.assertEqual(image.getpixel((10, 50))[:3], (255, 0, 0))
        self.assertEqual(image.getpixel((90, 50))[:3], (0, 0, 255))
        self.assertEqual(image.getpixel((48, 50))[:3], (255, 0, 0))
        self.assertEqual(image.getpixel((52, 50))[:3], (0, 0, 255))

    def test_closes_only_what_image_file_opened(self):
        from unittest import mock

        opened = BytesIO(png_bytes((10, 10)))
        with mock.patch.object(label_images, "image_file", return_value=opened):
            self.draw({"image": 7})
        self.assertTrue(opened.closed)
        logo = source((10, 10))
        self.draw({"image": "logo"}, logo_file=logo)
        self.assertFalse(logo.closed)
