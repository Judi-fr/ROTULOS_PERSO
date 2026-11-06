"""Lo que encontró la revisión completa de la app (2026-11-06)."""
from django.conf import settings
from django.test import SimpleTestCase, TestCase

from apps.accounts.auth_views import validate_password_strength
from apps.audit.services import clean_target_repr
from apps.carriers.catalog_views import ManualCarrierSerializer
from apps.carriers.models import ManualCarrier
from apps.labels.catalog_views import LabelSizeSerializer
from apps.labels.models import LabelSize


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
