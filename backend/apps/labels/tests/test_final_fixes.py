"""Ronda final de la fase 3: ``?ids=`` robusto, permisos de lectura de las
imágenes, ``template_id`` inválido, consultas del serializer y owned data."""

import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.accounts import team_services
from apps.accounts.tests.team_helpers import new_business
from apps.accounts.tests.test_team_permissions import add_employee

from ..models import LabelImage
from .test_labels import auth_headers_for

User = get_user_model()
URL = "/api/v1/labels/images/"


def png():
    from io import BytesIO

    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (4, 4)).save(output, format="PNG")
    return output.getvalue()


class FinalFixesTests(APITestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        subscriber, _ = Group.objects.get_or_create(name="subscriber")
        self.user = User.objects.create_user(username="f@example.com", email="f@example.com", password="Clave123!")
        self.user.groups.add(subscriber)
        self.biz = new_business(self.user)
        self.headers = auth_headers_for(self.user)

    def test_ids_with_unicode_digits_or_huge_numbers_do_not_500(self):
        for query in ("?ids=%C2%B2", "?ids=" + "9" * 40, "?ids=1,%C2%B2&template=%C2%B2", "?ids=1&template=" + "9" * 30):
            response = self.client.get(URL + query, **self.headers)
            self.assertEqual(response.status_code, 200, query)
            self.assertEqual(response.data, [])

    def test_ids_cap_limits_what_is_queried(self):
        captured = {}

        def fake_allowed(user, extra):
            return set(range(1, 1000))

        original = LabelImage.objects.filter

        def spy(*args, **kwargs):
            if "pk__in" in kwargs:
                captured["n"] = len(kwargs["pk__in"])
            return original(*args, **kwargs)

        with mock.patch("apps.labels.views.label_images.allowed_image_ids", fake_allowed), \
                mock.patch.object(LabelImage.objects, "filter", spy):
            ids = ",".join(str(i) for i in range(1, 500))
            self.client.get(f"{URL}?ids={ids}", **self.headers)
        self.assertEqual(captured["n"], 200)

    def test_preview_non_numeric_template_id_is_400(self):
        response = self.client.post(
            "/api/v1/labels/preview/",
            {"design": {}, "width_cm": 10, "height_cm": 15, "template_id": "abc"},
            format="json",
            **self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("template_id", response.data)

    def test_get_allowed_with_labels_edit_only_post_still_needs_template_create(self):
        employee, _ = add_employee(self.user, "emp@example.com", accesses=["templates"])
        real = __import__("apps.accounts.role_permissions", fromlist=["x"]).user_has_permission

        def fake(user, key):
            if key == "labels.template_create":
                return False
            return real(user, key)

        headers = auth_headers_for(employee)
        with mock.patch("apps.accounts.role_permissions.user_has_permission", fake):
            self.assertEqual(self.client.get(URL, **headers).status_code, 200)
            self.assertEqual(self.client.get(URL + "?ids=1", **headers).status_code, 200)
            self.assertEqual(self.client.post(URL, {}, format="json", **headers).status_code, 403)

    def test_template_save_without_image_ids_does_not_load_versions(self):
        from ..models import LabelTemplate

        template = LabelTemplate.objects.create(name="T", owner=self.biz, width_cm=10, height_cm=15, design={})
        with mock.patch.object(LabelTemplate, "versions", new_callable=mock.PropertyMock) as versions:
            from ..serializers import LabelTemplateSerializer

            request = mock.Mock(user=self.user)
            serializer = LabelTemplateSerializer(template, context={"request": request})
            serializer.validate_design({})
        versions.assert_not_called()

    def test_images_count_as_business_data(self):
        from django.core.files.base import ContentFile

        self.assertEqual(team_services.owned_data_count(self.biz), {})
        LabelImage.objects.create(owner=self.biz, image=ContentFile(png(), name="a.png"))
        self.assertEqual(team_services.owned_data_count(self.biz), {"imagen(es)": 1})


class ZplCodeWhiteBoxTests(APITestCase):
    SHAPE = {"id": "r", "kind": "rect", "left": 0, "top": 0, "width": 100, "height": 100, "fill": "#000000"}

    def zpl(self, design):
        from ..zpl import render_label_zpl

        return render_label_zpl(design, 10, 15, context={"pedido": "1"})

    def test_qr_over_a_shape_gets_a_white_box_before_bq(self):
        out = self.zpl({"shapes": [self.SHAPE], "qr": {"left": 10, "top": 10, "data": "abc", "size": 3}})
        white = out.index(",W,0^FS")
        self.assertLess(white, out.index("^BQ"))

    def test_barcode_over_a_shape_gets_a_white_box_before_bc(self):
        out = self.zpl({"shapes": [self.SHAPE], "barcode": {"left": 10, "top": 10, "data": "123456"}})
        self.assertLess(out.index(",W,0^FS"), out.index("^BC"))

    def test_shape_elsewhere_or_no_shapes_adds_no_white_box(self):
        far = dict(self.SHAPE, left=80, top=80, width=10, height=10)
        out = self.zpl({"shapes": [far], "qr": {"left": 5, "top": 5, "data": "abc", "size": 3}})
        self.assertNotIn(",W,0^FS", out)
        out = self.zpl({"qr": {"left": 5, "top": 5, "data": "abc", "size": 3},
                        "barcode": {"left": 5, "top": 50, "data": "123456"}})
        self.assertNotIn(",W,0^FS", out)


class ImageFailureLoggingTests(APITestCase):
    SHAPES = {"shapes": [{"id": "im", "kind": "image", "image": "logo", "left": 0, "top": 0, "width": 30, "height": 20}]}

    def test_failed_prepare_is_logged_in_zpl_and_pdf(self):
        from io import BytesIO

        from ..label_rendering import render_label_pdf
        from ..zpl import render_label_zpl

        for module, render in (
            ("apps.labels.shapes_zpl", lambda logo: render_label_zpl(self.SHAPES, 10, 15, logo_file=logo)),
            ("apps.labels.shapes_pdf", lambda logo: render_label_pdf(self.SHAPES, 10, 15, logo_file=logo)),
        ):
            with self.subTest(module=module), mock.patch("apps.labels.label_images.prepare", side_effect=RuntimeError("boom")):
                with self.assertLogs(module, level="WARNING") as logs:
                    render(BytesIO(png()))
                self.assertTrue(any(record.exc_info for record in logs.records))
