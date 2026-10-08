"""Tests de la conexión manual de Shopify: con la app que el comerciante creó
en su propio Dev Dashboard (client credentials), sin instalar la nuestra. La
API de Shopify se simula (``FakeShopify``)."""

import base64
import hashlib
import hmac
import json
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.integrations.events import process_due_events
from apps.integrations.models import IntegrationEvent, StoreConnection
from apps.integrations.providers import get_provider
from apps.integrations.tokens import access_token_for
from .test_shopify_connection import SECRET, SHOP, SHOPIFY_SETTINGS, WEBHOOKS_URL, ShopifyTestMixin
from apps.integrations.tests.helpers import auth_headers_for, make_user

MANUAL_URL = "/api/v1/integrations/shopify/connect-manual/"
OWN_ID = "id-de-la-app-propia"
OWN_SECRET = "secreto-de-la-app-propia"


@override_settings(**SHOPIFY_SETTINGS)
class ShopifyManualConnectTests(ShopifyTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")

    def _connect(self, **overrides):
        body = {"shop": "mitienda", "client_id": OWN_ID, "client_secret": OWN_SECRET}
        body.update(overrides)
        return self.client.post(MANUAL_URL, body, format="json", **auth_headers_for(self.user))

    def test_conecta_con_la_app_del_comerciante(self):
        response = self._connect()

        self.assertEqual(response.status_code, 201, response.data)
        connection = StoreConnection.objects.get(platform="shopify", external_store_id=SHOP)
        self.assertEqual(connection.owner, self.user)
        self.assertEqual(connection.access_token, "propio-1")
        self.assertEqual(connection.webhook_secret, OWN_SECRET)
        self.assertEqual(connection.preferences["own_app_client_id"], OWN_ID)
        self.assertIsNotNone(connection.token_expires_at)
        token_call = self.fake.token_calls()[0]["data"]
        self.assertEqual(
            token_call, {"grant_type": "client_credentials", "client_id": OWN_ID, "client_secret": OWN_SECRET}
        )
        # Webhooks e importación los hace el worker, como en la conexión normal.
        self.assertTrue(IntegrationEvent.objects.filter(connection=connection, event_type="internal/store_setup").exists())

    def test_app_de_otra_organizacion_explica_por_que(self):
        self.fake.client_credentials_error = "shop_not_permitted"

        response = self._connect()

        self.assertEqual(response.status_code, 400)
        self.assertIn("misma organización", response.data["detail"])
        self.assertFalse(StoreConnection.objects.exists())

    def test_credenciales_que_shopify_no_reconoce(self):
        self.fake.client_credentials_error = "invalid_client"
        response = self._connect()
        self.assertEqual(response.status_code, 400)
        self.assertIn("no reconoce", response.data["detail"])

    def test_id_de_app_inexistente_con_el_texto_real_de_shopify(self):
        self.fake.client_credentials_error = "Could not find Shopify API application with api_key xyz"
        response = self._connect()
        self.assertEqual(response.status_code, 400)
        self.assertIn("no reconoce", response.data["detail"])

    def test_faltan_datos(self):
        self.assertEqual(self._connect(client_secret="").status_code, 400)
        self.assertEqual(self._connect(shop="no es un dominio").status_code, 400)
        self.assertEqual(self.fake.token_calls(), [])

    def test_sin_sesion_no_se_puede(self):
        self.client.credentials()
        response = self.client.post(MANUAL_URL, {"shop": "mitienda"}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_el_token_se_renueva_con_las_credenciales_de_su_app(self):
        self._connect()
        connection = StoreConnection.objects.get(external_store_id=SHOP)
        connection.token_expires_at = timezone.now() + timedelta(seconds=10)
        connection.save(update_fields=["token_expires_at"])

        token = access_token_for(connection, get_provider("shopify"))

        self.assertEqual(token, "propio-2")
        renewal = self.fake.token_calls()[-1]["data"]
        self.assertEqual((renewal["grant_type"], renewal["client_secret"]), ("client_credentials", OWN_SECRET))

    def test_instalar_despues_nuestra_app_descarta_la_app_propia(self):
        self._connect()
        connection = StoreConnection.objects.get(external_store_id=SHOP)
        from apps.integrations.stores import connect_store

        connect_store(get_provider("shopify"), "codigo", self.user, shop_domain=SHOP)

        connection.refresh_from_db()
        self.assertNotIn("own_app_client_id", connection.preferences)
        self.assertEqual(connection.webhook_secret, "")


@override_settings(**SHOPIFY_SETTINGS)
class ShopifyOwnAppWebhookTests(ShopifyTestMixin, APITestCase):
    def _post(self, topic, payload, *, secret):
        body = json.dumps(payload).encode("utf-8")
        signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
        return self.client.generic(
            "POST",
            WEBHOOKS_URL,
            body,
            content_type="application/json",
            HTTP_X_SHOPIFY_HMAC_SHA256=signature,
            HTTP_X_SHOPIFY_TOPIC=topic,
            HTTP_X_SHOPIFY_SHOP_DOMAIN=SHOP,
        )

    def _own_app_store(self):
        connection = self._connection(owner=make_user("comercio@example.com"), preferences={"own_app_client_id": OWN_ID})
        connection.webhook_secret = OWN_SECRET
        connection.save()
        return connection

    def test_aviso_firmado_por_la_app_del_comerciante_se_acepta(self):
        connection = self._own_app_store()

        response = self._post("app/uninstalled", {"id": 1}, secret=OWN_SECRET)
        process_due_events()

        self.assertEqual(response.status_code, 200)
        connection.refresh_from_db()
        self.assertEqual(connection.status, StoreConnection.Status.REVOKED)

    def test_aviso_firmado_por_nuestra_app_sigue_andando(self):
        self._own_app_store()
        self.assertEqual(self._post("app/uninstalled", {"id": 1}, secret=SECRET).status_code, 200)

    def test_el_secreto_de_otra_app_no_sirve(self):
        self._own_app_store()
        self.assertEqual(self._post("app/uninstalled", {"id": 1}, secret="cualquiera").status_code, 401)
        self.assertFalse(IntegrationEvent.objects.exists())

    def test_tienda_conectada_con_nuestra_app_no_acepta_otro_secreto(self):
        connection = self._connection(owner=make_user("comercio@example.com"))
        connection.webhook_secret = OWN_SECRET  # sin app propia, no cuenta
        connection.save()
        self.assertEqual(self._post("app/uninstalled", {"id": 1}, secret=OWN_SECRET).status_code, 401)
