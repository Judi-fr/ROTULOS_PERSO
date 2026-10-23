"""Datos de la cuenta (T#351-353): API, y su uso como remitente, logo y
plantilla por defecto y en la planilla de retiro."""

import shutil
import tempfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import override_settings
from PIL import Image
from pypdf import PdfReader
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit.models import AuditLog
from apps.carriers.models import ManualCarrier
from apps.integrations.models import StoreConnection
from apps.labels.batch_views import LabelBatchView, _render_params_for_item
from apps.labels.label_rendering import build_label_context
from apps.labels.models import LabelTemplate
from apps.orders.manifest import render_manifest_pdf
from apps.orders.models import Address, Order

from ..models import AccountSettings, cuit_is_valid
from apps.accounts.tests.team_helpers import give_solo_teams

User = get_user_model()
URL = "/api/v1/auth/me/account/"


def png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return buffer.getvalue()


class AccountSettingsTestCase(APITestCase):
    def setUp(self):
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        media_override = override_settings(MEDIA_ROOT=media)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.user = User.objects.create_user(username="cuenta@example.com", email="cuenta@example.com", password="Clave123!")
        self.user.groups.add(Group.objects.get_or_create(name="subscriber")[0])
        self.headers = {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(self.user).access_token}"}
        give_solo_teams()  # comercio "solo" (team_helpers, transitorio)

    def patch(self, data):
        return self.client.patch(URL, data, format="json", **self.headers)


class AccountSettingsApiTests(AccountSettingsTestCase):
    def test_empty_account_reads_blank_without_creating_a_row(self):
        response = self.client.get(URL, **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["legal_name"], "")
        self.assertFalse(AccountSettings.objects.exists())

    def test_patch_creates_normalizes_cuit_and_audits(self):
        response = self.patch({"legal_name": "Mi Tienda SRL", "tax_id": "30536259194"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["tax_id"], "30-53625919-4")
        log = AuditLog.objects.get(action="account.update")
        self.assertEqual(log.changes["tax_id"], {"from": "", "to": "30-53625919-4"})

    def test_invalid_cuit(self):
        self.assertIn("tax_id", self.patch({"tax_id": "30-53625919-5"}).data)
        self.assertFalse(cuit_is_valid("123"))

    def test_default_template_must_be_usable(self):
        foreign = LabelTemplate.objects.create(name="Ajena", width_cm=10, height_cm=15, design={})
        self.assertIn("default_template", self.patch({"default_template": foreign.pk}).data)
        own = LabelTemplate.objects.create(name="Mía", owner=self.user, width_cm=10, height_cm=15, design={})
        self.assertEqual(self.patch({"default_template": own.pk}).data["default_template"], own.pk)

    def test_default_carrier_is_an_api_code_or_a_catalog_name(self):
        self.assertEqual(self.patch({"default_carrier": "oca"}).data["default_carrier"], "oca")
        ManualCarrier.objects.create(name="Via Cargo")
        self.assertEqual(self.patch({"default_carrier": "via cargo"}).data["default_carrier"], "Via Cargo")
        self.assertIn("default_carrier", self.patch({"default_carrier": "Inventado"}).data)

    def test_logo_as_data_url(self):
        import base64

        data_url = "data:image/png;base64," + base64.b64encode(png_bytes()).decode()
        response = self.patch({"logo": data_url})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(AccountSettings.objects.get(user=self.user).logo)


class AccountSettingsUsageTests(AccountSettingsTestCase):
    def setUp(self):
        super().setUp()
        self.account = AccountSettings.objects.create(
            user=self.user,
            legal_name="Mi Tienda SRL",
            tax_id="30-53625919-4",
            sender_name="Depósito Central",
            sender_address="Av. Siempre Viva 742, CABA",
            sender_phone="11 5555-0000",
        )
        self.address = Address.objects.create(user=self.user, recipient_name="María", street="Corrientes", number="1000", city="CABA")
        give_solo_teams()  # comercio "solo" (team_helpers, transitorio)

    def test_manual_order_uses_account_sender(self):
        order = Order.objects.create(user=self.user, address=self.address)
        context = build_label_context(order=order)
        self.assertEqual(context["remitente"], "Depósito Central")
        self.assertEqual(context["remitente_domicilio"], "Av. Siempre Viva 742, CABA")
        self.assertEqual(context["remitente_telefono"], "11 5555-0000")

    def test_store_data_wins_and_account_fills_the_gaps(self):
        store = StoreConnection.objects.create(
            owner=self.user, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="9", name="Tienda X", sender_phone="11 4444-1111"
        )
        order = Order.objects.create(user=self.user, address=self.address, store_connection=store, external_id="1")
        context = build_label_context(order=order)
        self.assertEqual(context["remitente"], "Depósito Central")
        self.assertEqual(context["remitente_telefono"], "11 4444-1111")
        store.sender_name = "Tienda X Envíos"
        store.save()
        self.assertEqual(build_label_context(order=Order.objects.get(pk=order.pk))["remitente"], "Tienda X Envíos")

    def test_logo_falls_back_to_the_account(self):
        self.account.logo.save("logo.png", ContentFile(png_bytes()), save=True)
        order = Order.objects.create(user=self.user, address=self.address)
        template = LabelTemplate.objects.filter(is_public=True).first()
        *_, logo_file, _ = _render_params_for_item(order, template)
        self.assertEqual(logo_file.name, self.account.logo.name)

    def test_batch_falls_back_to_the_account_template(self):
        own = LabelTemplate.objects.create(name="Mía", owner=self.user, width_cm=10, height_cm=10, design={})
        self.account.default_template = own
        self.account.save()
        order = Order.objects.create(user=self.user, address=self.address)
        self.assertEqual(LabelBatchView()._resolve_template(self.user, None, [order]), own)

    def test_manifest_shows_legal_name_and_cuit(self):
        order = Order.objects.create(user=self.user, address=self.address)
        pdf = render_manifest_pdf([order], self.user, carrier="OCA")
        text = "".join(page.extract_text() for page in PdfReader(BytesIO(pdf)).pages)
        self.assertIn("Mi Tienda SRL · CUIT 30-53625919-4", text)
        self.assertIn("Depósito Central", text)
