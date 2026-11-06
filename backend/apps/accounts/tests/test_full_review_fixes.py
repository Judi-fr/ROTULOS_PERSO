"""Lo que encontró la revisión completa de la app (2026-11-06)."""
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import Throttled

from apps.accounts.auth_views import validate_password_strength
from apps.audit.services import clean_target_repr
from apps.carriers.catalog_views import ManualCarrierSerializer
from apps.carriers.models import ManualCarrier
from apps.labels.catalog_views import LabelSizeSerializer
from apps.labels.models import LabelSize
from config.errors import api_exception_handler


class ThrottleMessageTests(SimpleTestCase):
    def test_el_limite_se_explica_sin_palabras_tecnicas(self):
        self.assertEqual(
            api_exception_handler(Throttled(wait=37.2), {}).data["detail"],
            "Demasiados intentos seguidos. Probá de nuevo en 38 segundos.",
        )
        self.assertIn("en 5 minutos", api_exception_handler(Throttled(wait=300), {}).data["detail"])
        self.assertIn("en un rato", api_exception_handler(Throttled(), {}).data["detail"])


class InternalUserInActivityTests(SimpleTestCase):
    def test_saca_el_usuario_interno_del_comercio(self):
        self.assertEqual(clean_target_repr("Pedido #90 (comercio-917442dd80ed@datos.invalid)"), "Pedido #90")

    def test_no_toca_el_resto(self):
        self.assertEqual(clean_target_repr("Pedido #90 (ana@example.com)"), "Pedido #90 (ana@example.com)")
        self.assertEqual(clean_target_repr(None), "")


class UploadLimitTests(SimpleTestCase):
    def test_entra_una_imagen_de_2_mb_en_base64(self):
        as_base64 = 2 * 1024 * 1024 * 4 / 3
        self.assertGreater(settings.DATA_UPLOAD_MAX_MEMORY_SIZE, as_base64)


class PasswordRuleTests(SimpleTestCase):
    def test_el_mensaje_dice_el_minimo_real(self):
        self.assertIn("8 caracteres", validate_password_strength("Abc1"))
        self.assertIsNone(validate_password_strength("Abcdefg1"))


class CatalogDuplicateMessageTests(TestCase):
    def test_transportista_repetido(self):
        ManualCarrier.objects.create(name="Cruz")
        serializer = ManualCarrierSerializer(data={"name": "Cruz"})
        self.assertFalse(serializer.is_valid())
        self.assertEqual(str(serializer.errors["name"][0]), "Ya hay un transportista con ese nombre.")

    def test_tamano_repetido(self):
        LabelSize.objects.create(name="Chico", width_cm=10, height_cm=5)
        serializer = LabelSizeSerializer(data={"name": "Chico", "width_cm": "10", "height_cm": "5"})
        self.assertFalse(serializer.is_valid())
        self.assertEqual(str(serializer.errors["name"][0]), "Ya hay un tamaño con ese nombre.")
