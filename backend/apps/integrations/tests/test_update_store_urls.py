"""``update_store_urls``: después de cambiar de dominio, cada tienda activa
vuelve a registrar sus webhooks y la de Tiendanube con medio de envío
actualiza sus callbacks."""

from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from apps.accounts.tests.team_helpers import new_business
from apps.integrations.models import IntegrationEvent, StoreConnection
from apps.integrations.providers.base import ProviderError
from apps.integrations.providers.tiendanube import labels as store_labels
from apps.integrations.stores import STORE_SETUP_EVENT
from apps.integrations.tests.helpers import make_user

REGISTER = "apps.integrations.management.commands.update_store_urls.store_labels.register_carrier"


@override_settings(INTEGRATIONS_PUBLIC_BASE_URL="https://rotulos.nuevo.com.ar")
class UpdateStoreUrlsTests(TestCase):
    def setUp(self):
        owner = new_business(make_user("comercio@example.com"))
        self.carrier_store = StoreConnection.objects.create(
            platform="tiendanube", external_store_id="111", owner=owner,
            preferences={store_labels.CARRIER_ID_PREFERENCE: "55"},
        )
        self.plain_store = StoreConnection.objects.create(platform="woocommerce", external_store_id="https://tienda.com.ar", owner=owner)
        StoreConnection.objects.create(
            platform="tiendanube", external_store_id="222", owner=owner, status=StoreConnection.Status.REVOKED,
        )

    def run_command(self, *args):
        out = StringIO()
        call_command("update_store_urls", *args, stdout=out)
        return out.getvalue()

    def setup_events(self):
        return IntegrationEvent.objects.filter(event_type=STORE_SETUP_EVENT)

    def test_reapunta_las_tiendas_activas(self):
        with patch(REGISTER) as register:
            output = self.run_command()

        self.assertEqual(
            sorted(self.setup_events().values_list("connection_id", flat=True)),
            [self.carrier_store.pk, self.plain_store.pk],
        )
        register.assert_called_once_with(self.carrier_store)
        self.assertIn("medio de envío actualizado", output)

    def test_simulacion_no_cambia_nada(self):
        with patch(REGISTER) as register:
            output = self.run_command("--dry-run")

        self.assertFalse(self.setup_events().exists())
        register.assert_not_called()
        self.assertIn("simulación", output)

    def test_un_medio_de_envio_que_falla_no_frena_a_las_demas(self):
        with patch(REGISTER, side_effect=ProviderError("Tiendanube no contesta")):
            with self.assertRaisesMessage(CommandError, "1 tienda(s)"):
                self.run_command()
        self.assertEqual(self.setup_events().count(), 2)

    @override_settings(INTEGRATIONS_PUBLIC_BASE_URL="")
    def test_sin_url_publica_no_hace_nada(self):
        with self.assertRaisesMessage(CommandError, "INTEGRATIONS_PUBLIC_BASE_URL"):
            self.run_command()
        self.assertFalse(self.setup_events().exists())
