"""Perfil personal (``PersonalProfile``): nombre para mostrar, teléfono, puesto
y foto propia, editados en ``me/`` y vistos por el equipo."""

import base64
import shutil
import tempfile
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework.test import APITestCase

from apps.accounts.models import PersonalProfile
from apps.accounts.personal_profiles import AVATAR_SIZE_PX
from apps.audit.services import record

from .test_team_permissions import add_employee, bearer, make_user
from apps.accounts.tests.team_helpers import new_business

ME = "/api/v1/auth/me/"


def image_bytes(size=(40, 20), mode="RGB", color="red", fmt="PNG"):
    buffer = BytesIO()
    Image.new(mode, size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def data_url(raw, mime="image/png"):
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


class PersonalProfileTestCase(APITestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        media_override = override_settings(MEDIA_ROOT=media)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.user = make_user("ana@example.com")
        new_business(self.user)
        self.user.first_name, self.user.last_name = "Ana", "Pérez"
        self.user.save()

    def patch(self, data, user=None, fmt="json"):
        return self.client.patch(ME, data, format=fmt, **bearer(user or self.user))


class ProfileFieldsTests(PersonalProfileTestCase):
    def test_reading_without_a_profile_creates_nothing(self):
        response = self.client.get(ME, **bearer(self.user))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {key: response.data[key] for key in ("preferred_name", "phone", "job_title", "photo", "display_name")},
            {"preferred_name": "", "phone": "", "job_title": "", "photo": None, "display_name": "Ana Pérez"},
        )
        self.assertFalse(PersonalProfile.objects.exists())

    def test_saves_name_phone_and_job_title(self):
        response = self.patch({"preferred_name": " Anita ", "phone": "+54 11 4444-5555", "job_title": "Depósito"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["display_name"], "Anita")
        self.assertEqual(response.data["phone"], "+54 11 4444-5555")
        self.assertEqual(response.data["job_title"], "Depósito")
        profile = PersonalProfile.objects.get(user=self.user)
        self.assertEqual(profile.preferred_name, "Anita")

    def test_a_partial_patch_keeps_the_rest(self):
        self.patch({"preferred_name": "Anita", "job_title": "Ventas"})
        response = self.patch({"phone": "123"})
        self.assertEqual((response.data["preferred_name"], response.data["job_title"]), ("Anita", "Ventas"))

    def test_phone_rejects_letters(self):
        response = self.patch({"phone": "llamame al 123"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("phone", response.data)

    def test_email_and_staff_stay_out_of_reach(self):
        self.patch({"email": "otra@example.com", "is_staff": True, "preferred_name": "Ana"})
        self.user.refresh_from_db()
        self.assertEqual((self.user.email, self.user.is_staff), ("ana@example.com", False))

    def test_each_one_edits_only_their_own(self):
        other = make_user("otro@example.com")
        self.patch({"preferred_name": "Otro"}, user=other)
        self.assertFalse(PersonalProfile.objects.filter(user=self.user).exists())
        self.assertEqual(PersonalProfile.objects.get(user=other).preferred_name, "Otro")

    def test_login_payload_carries_display_name_and_photo(self):
        self.patch({"preferred_name": "Anita", "photo": data_url(image_bytes())})
        response = self.client.post(
            "/api/v1/auth/login/", {"email": "ana@example.com", "password": "Clave123!"}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["user"]["display_name"], "Anita")
        self.assertRegex(response.data["user"]["photo"], r"^http://testserver/media/accounts/avatars/[0-9a-f]{32}\.jpg$")


class PhotoTests(PersonalProfileTestCase):
    def stored(self):
        return Image.open(PersonalProfile.objects.get(user=self.user).photo.path)

    def test_photo_as_data_url_is_cropped_square_and_gets_a_random_name(self):
        response = self.patch({"photo": data_url(image_bytes(size=(400, 200)))})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertRegex(response.data["photo"], r"/media/accounts/avatars/[0-9a-f]{32}\.jpg$")
        image = self.stored()
        self.assertEqual((image.format, image.size), ("JPEG", (AVATAR_SIZE_PX, AVATAR_SIZE_PX)))

    def test_photo_as_multipart_file(self):
        upload = SimpleUploadedFile("ana-perez.png", image_bytes(), content_type="image/png")
        response = self.patch({"photo": upload}, fmt="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("ana", response.data["photo"])

    def test_transparent_background_becomes_white_not_black(self):
        self.patch({"photo": data_url(image_bytes(mode="RGBA", color=(0, 0, 0, 0)))})
        self.assertEqual(self.stored().getpixel((5, 5)), (255, 255, 255))

    def test_rejects_what_is_not_an_image(self):
        response = self.patch({"photo": data_url(b"no soy una imagen")})
        self.assertEqual(response.status_code, 400)
        self.assertIn("photo", response.data)
        self.assertFalse(PersonalProfile.objects.filter(user=self.user).exclude(photo="").exists())

    def test_rejects_a_photo_over_the_limit(self):
        big = SimpleUploadedFile("big.png", image_bytes() + b"0" * (2 * 1024 * 1024 + 1), content_type="image/png")
        response = self.patch({"photo": big}, fmt="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("photo", response.data)

    def test_replacing_or_removing_deletes_the_old_file(self):
        self.patch({"photo": data_url(image_bytes())})
        first = PersonalProfile.objects.get(user=self.user).photo
        storage, first_name = first.storage, first.name
        self.patch({"photo": data_url(image_bytes(color="blue"))})
        self.assertFalse(storage.exists(first_name))
        second_name = PersonalProfile.objects.get(user=self.user).photo.name

        response = self.patch({"photo": None})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["photo"])
        self.assertFalse(storage.exists(second_name))

    def test_saving_other_fields_keeps_the_photo(self):
        self.patch({"photo": data_url(image_bytes())})
        response = self.patch({"job_title": "Ventas"})
        self.assertIsNotNone(response.data["photo"])


class TeamSeesProfileTests(PersonalProfileTestCase):
    def test_members_and_activity_show_name_job_title_and_photo(self):
        employee, _ = add_employee(self.user, "dep@example.com", profile="Depósito")
        self.patch({"preferred_name": "Juancho", "job_title": "Depósito", "photo": data_url(image_bytes())}, user=employee)
        record(actor=employee, category="orders", action="order.ship", target_repr="Pedido 1")

        members = self.client.get("/api/v1/team/", **bearer(self.user)).data["members"]
        shown = next(member for member in members if member["email"] == "dep@example.com")
        self.assertEqual((shown["display_name"], shown["job_title"]), ("Juancho", "Depósito"))
        self.assertRegex(shown["photo"], r"/media/accounts/avatars/")
        owner = next(member for member in members if member["email"] == "ana@example.com")
        self.assertEqual((owner["display_name"], owner["photo"]), ("Ana Pérez", None))

        rows = self.client.get("/api/v1/team/activity/", **bearer(self.user)).data["results"]
        row = next(row for row in rows if row["action"] == "order.ship")
        self.assertEqual(row["actor"]["display_name"], "Juancho")
        self.assertEqual(row["actor"]["job_title"], "Depósito")
