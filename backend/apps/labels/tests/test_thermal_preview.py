"""Vista térmica del editor (fase 4): ``POST /labels/preview/`` con
``"thermal": true`` devuelve el mismo dibujo como lo imprime una Zebra: un PNG
de 1 bit a 203 dpi con la regla térmica de las formas (oscuro → negro, claro →
nada, en medio una trama)."""

from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from PIL import Image
from rest_framework.test import APITestCase

from apps.accounts.tests.team_helpers import new_business

User = get_user_model()
URL = "/api/v1/labels/preview/"


def rect(fill, left=0, top=0, width=100, height=100):
    return {"shapes": [{"id": "r", "kind": "rect", "left": left, "top": top, "width": width, "height": height,
                        "fill": fill}]}


class ThermalPreviewTests(APITestCase):
    def setUp(self):
        user = User.objects.create_user(username="t@example.com", email="t@example.com", password="Clave123!")
        group, _ = Group.objects.get_or_create(name="subscriber")
        user.groups.add(group)
        new_business(user)
        self.client.force_authenticate(user=user)

    def thermal(self, design, width_cm=5, height_cm=5):
        response = self.client.post(URL, {"design": design, "width_cm": width_cm, "height_cm": height_cm,
                                          "thermal": True}, format="json")
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        self.assertEqual(response["Content-Type"], "image/png")
        return Image.open(BytesIO(response.content))

    def black_ratio(self, image):
        pixels = list(image.convert("L").getdata())
        return sum(1 for value in pixels if value < 128) / len(pixels)

    def test_size_is_203_dpi(self):
        image = self.thermal({}, width_cm=10, height_cm=15)
        self.assertEqual(image.size, (800, 1200))  # 8 dots per mm
        self.assertEqual(image.mode, "1")

    def test_black_fill_prints_black(self):
        self.assertGreater(self.black_ratio(self.thermal(rect("#000000"))), 0.97)

    def test_very_light_fill_prints_nothing(self):
        # Luminancia > 245: la Zebra no pone ningún punto.
        self.assertLess(self.black_ratio(self.thermal(rect("#fafafa"))), 0.01)

    def test_almost_white_gets_one_dot_in_sixteen_like_zpl(self):
        # #f3f4f6 (luminancia 243) en ZPL: tinta donde bayer < tono*16, o sea
        # solo las celdas 0 → 1 de cada 16.
        ratio = self.black_ratio(self.thermal(rect("#f3f4f6")))
        self.assertAlmostEqual(ratio, 1 / 16, delta=0.01)

    def test_mid_gray_is_a_screen(self):
        ratio = self.black_ratio(self.thermal(rect("#9ca3af")))
        self.assertGreater(ratio, 0.2)
        self.assertLess(ratio, 0.8)

    def test_without_thermal_still_pdf(self):
        response = self.client.post(URL, {"design": {}, "width_cm": 5, "height_cm": 5}, format="json")
        self.assertEqual(response["Content-Type"], "application/pdf")
