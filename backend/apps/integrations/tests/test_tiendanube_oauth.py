"""Tests de la instalación de la app en Tiendanube (fase 2): URL de
autorización, callback OAuth, vínculo de la tienda con una cuenta y
desconexión. La API de Tiendanube se simula: nunca sale una llamada real."""

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import requests
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import signing
from django.test import override_settings
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit.models import AuditLog
from apps.orders.ingestion import upsert_store_order

from ..models import StoreConnection
from ..providers import get_provider
from ..stores import STATE_SALT, make_claim_token, make_oauth_state
from .test_store_integration_base import tiendanube_order

User = get_user_model()

CALLBACK_URL = "/api/v1/integrations/tiendanube/callback/"
INSTALL_URL = "/api/v1/integrations/tiendanube/install-url/"
STORES_URL = "/api/v1/integrations/stores/"
FRONTEND = "http://localhost:8001"


def auth_headers_for(user):
    token = RefreshToken.for_user(user)
    return {"HTTP_AUTHORIZATION": f"Bearer {token.access_token}"}


def fake_response(status_code=200, json_data=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {} if json_data is None else json_data
    return response


def make_user(email):
    user = User.objects.create_user(username=email, email=email, password="Clave123!")
    group, _ = Group.objects.get_or_create(name="subscriber")
    user.groups.add(group)
    return user


TEST_SETTINGS = {
    "TIENDANUBE_APP_ID": "9999",
    "TIENDANUBE_CLIENT_SECRET": "secreto-de-prueba",
    "TIENDANUBE_USER_AGENT": "ROTULOS_PERSO (soporte@example.com)",
    "TIENDANUBE_API_VERSION": "2025-03",
    "FRONTEND_URL": FRONTEND,
    "STORE_CONNECT_FRONTEND_PATH": "integraciones.html",
}


@override_settings(**TEST_SETTINGS)
class TiendanubeInstallUrlTests(APITestCase):
    def setUp(self):
        self.user = make_user("comercio@example.com")

    def test_requiere_estar_logueado(self):
        response = self.client.get(INSTALL_URL)
        self.assertEqual(response.status_code, 401)

    def test_url_con_app_id_y_state_firmado_del_usuario(self):
        response = self.client.get(INSTALL_URL, **auth_headers_for(self.user))

        self.assertEqual(response.status_code, 200, response.data)
        url = response.data["authorize_url"]
        self.assertTrue(url.startswith("https://www.tiendanube.com/apps/9999/authorize?"))
        state = parse_qs(urlparse(url).query)["state"][0]
        self.assertEqual(signing.loads(state, salt=STATE_SALT)["u"], self.user.pk)

    @override_settings(TIENDANUBE_APP_ID="")
    def test_sin_app_configurada_devuelve_503(self):
        response = self.client.get(INSTALL_URL, **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 503)


@override_settings(**TEST_SETTINGS)
class TiendanubeCallbackTests(APITestCase):
    def setUp(self):
        self.user = make_user("comercio@example.com")
        self.other = make_user("otro@example.com")

        post_patcher = patch("apps.integrations.providers.tiendanube.requests.post")
        request_patcher = patch("apps.integrations.providers.tiendanube.requests.request")
        self.mock_post = post_patcher.start()
        self.mock_request = request_patcher.start()
        self.addCleanup(post_patcher.stop)
        self.addCleanup(request_patcher.stop)

        self.mock_post.return_value = self._token_response("tok-1")
        self.mock_request.return_value = fake_response(
            200,
            {
                "id": 555,
                "name": {"es": "Tienda de prueba", "pt": "Loja de teste"},
                "main_language": "es",
                "original_domain": "prueba.mitiendanube.com",
                "domains": [],
            },
        )

    @staticmethod
    def _token_response(token):
        return fake_response(
            200,
            {"access_token": token, "token_type": "bearer", "scope": "read_orders,write_orders", "user_id": "555"},
        )

    def _callback(self, **params):
        return self.client.get(CALLBACK_URL, params)

    def _redirect_params(self, response):
        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertTrue(location.startswith(f"{FRONTEND}/integraciones.html?"), location)
        return {key: values[0] for key, values in parse_qs(urlparse(location).query).items()}

    def test_desde_nuestra_web_la_tienda_queda_vinculada_al_usuario(self):
        response = self._callback(code="abc", state=make_oauth_state(self.user, "tiendanube"))

        params = self._redirect_params(response)
        connection = StoreConnection.objects.get()
        self.assertEqual(params, {"store_connected": str(connection.pk)})
        self.assertEqual(connection.owner, self.user)
        self.assertEqual(connection.external_store_id, "555")
        self.assertEqual(connection.status, StoreConnection.Status.ACTIVE)
        self.assertEqual(connection.access_token, "tok-1")
        self.assertNotIn("tok-1", connection.access_token_encrypted)
        self.assertEqual(connection.name, "Tienda de prueba")
        self.assertEqual(connection.store_url, "https://prueba.mitiendanube.com")
        self.assertTrue(AuditLog.objects.filter(action="store.connect", actor=self.user).exists())

        sent = self.mock_post.call_args.kwargs["json"]
        self.assertEqual(sent["client_id"], "9999")
        self.assertEqual(sent["code"], "abc")
        self.assertEqual(sent["grant_type"], "authorization_code")

    def test_la_api_se_llama_con_user_agent_y_token_de_la_tienda(self):
        self._callback(code="abc", state=make_oauth_state(self.user, "tiendanube"))

        args, kwargs = self.mock_request.call_args
        self.assertEqual(args, ("GET", "https://api.tiendanube.com/2025-03/555/store"))
        self.assertEqual(kwargs["headers"]["User-Agent"], "ROTULOS_PERSO (soporte@example.com)")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok-1")

    def test_el_token_no_viaja_en_la_redireccion(self):
        response = self._callback(code="abc", state=make_oauth_state(self.user, "tiendanube"))
        self.assertNotIn("tok-1", response["Location"])

    def test_desde_la_tienda_de_apps_queda_pendiente_y_se_reclama_al_loguearse(self):
        params = self._redirect_params(self._callback(code="abc"))

        connection = StoreConnection.objects.get()
        self.assertIsNone(connection.owner)
        self.assertIn("store_claim", params)

        response = self.client.post(
            f"{STORES_URL}claim/", {"token": params["store_claim"]}, format="json", **auth_headers_for(self.user)
        )
        self.assertEqual(response.status_code, 200, response.data)
        connection.refresh_from_db()
        self.assertEqual(connection.owner, self.user)
        self.assertTrue(AuditLog.objects.filter(action="store.claim", actor=self.user).exists())

        # Reintentar el mismo reclamo es inofensivo.
        again = self.client.post(
            f"{STORES_URL}claim/", {"token": params["store_claim"]}, format="json", **auth_headers_for(self.user)
        )
        self.assertEqual(again.status_code, 200)

    def test_otra_cuenta_no_puede_reclamar_una_tienda_ya_vinculada(self):
        params = self._redirect_params(self._callback(code="abc"))
        self.client.post(
            f"{STORES_URL}claim/", {"token": params["store_claim"]}, format="json", **auth_headers_for(self.user)
        )

        response = self.client.post(
            f"{STORES_URL}claim/", {"token": params["store_claim"]}, format="json", **auth_headers_for(self.other)
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(StoreConnection.objects.get().owner, self.user)

    def test_reclamo_con_token_alterado_o_vencido_se_rechaza(self):
        self._callback(code="abc")
        connection = StoreConnection.objects.get()

        tampered = self.client.post(
            f"{STORES_URL}claim/", {"token": make_claim_token(connection) + "x"}, format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(tampered.status_code, 400)

        with override_settings(INTEGRATIONS_STORE_CLAIM_MAX_AGE_SECONDS=-1):
            expired = self.client.post(
                f"{STORES_URL}claim/", {"token": make_claim_token(connection)}, format="json",
                **auth_headers_for(self.user),
            )
        self.assertEqual(expired.status_code, 400)
        connection.refresh_from_db()
        self.assertIsNone(connection.owner)

    def test_reinstalar_actualiza_el_token_sin_duplicar_la_tienda(self):
        state = make_oauth_state(self.user, "tiendanube")
        self._callback(code="abc", state=state)
        self.mock_post.return_value = self._token_response("tok-2")
        self._callback(code="def", state=state)

        connection = StoreConnection.objects.get()
        self.assertEqual(connection.access_token, "tok-2")
        self.assertEqual(connection.owner, self.user)

    def test_reinstalar_desde_otra_cuenta_no_le_pasa_la_tienda(self):
        self._callback(code="abc", state=make_oauth_state(self.user, "tiendanube"))
        params = self._redirect_params(self._callback(code="def", state=make_oauth_state(self.other, "tiendanube")))

        self.assertEqual(params, {"store_error": "owned_by_other_account"})
        self.assertEqual(StoreConnection.objects.get().owner, self.user)

    def test_state_alterado_no_conecta_nada(self):
        params = self._redirect_params(self._callback(code="abc", state="state-inventado"))

        self.assertEqual(params, {"store_error": "invalid_state"})
        self.mock_post.assert_not_called()
        self.assertFalse(StoreConnection.objects.exists())

    def test_codigo_vencido_o_usado(self):
        self.mock_post.return_value = fake_response(
            200, {"error": "invalid_grant", "error_description": "The authorization code has expired"}
        )
        params = self._redirect_params(self._callback(code="viejo", state=make_oauth_state(self.user, "tiendanube")))

        self.assertEqual(params, {"store_error": "authorization_rejected"})
        self.assertFalse(StoreConnection.objects.exists())

    def test_tiendanube_no_responde(self):
        self.mock_post.side_effect = requests.ConnectionError("sin red")
        params = self._redirect_params(self._callback(code="abc", state=make_oauth_state(self.user, "tiendanube")))

        self.assertEqual(params, {"store_error": "provider_unavailable"})
        self.assertFalse(StoreConnection.objects.exists())

    def test_sin_codigo_o_instalacion_cancelada(self):
        self.assertEqual(self._redirect_params(self._callback()), {"store_error": "missing_code"})
        self.assertEqual(
            self._redirect_params(self._callback(error="access_denied")),
            {"store_error": "authorization_cancelled"},
        )

    def test_si_no_se_pueden_leer_los_datos_de_la_tienda_igual_queda_conectada(self):
        self.mock_request.return_value = fake_response(500)
        params = self._redirect_params(self._callback(code="abc", state=make_oauth_state(self.user, "tiendanube")))

        connection = StoreConnection.objects.get()
        self.assertEqual(params, {"store_connected": str(connection.pk)})
        self.assertIn("No se pudieron leer", connection.last_error)


@override_settings(**TEST_SETTINGS)
class StoreConnectionApiTests(APITestCase):
    def setUp(self):
        self.user = make_user("comercio@example.com")
        self.other = make_user("otro@example.com")
        self.mine = StoreConnection.objects.create(
            owner=self.user, platform="tiendanube", external_store_id="111", name="Mía", access_token="tok-mio"
        )
        self.theirs = StoreConnection.objects.create(
            owner=self.other, platform="tiendanube", external_store_id="222", name="Ajena", access_token="tok-ajeno"
        )

    def test_lista_solo_las_tiendas_propias_y_sin_token(self):
        response = self.client.get(STORES_URL, **auth_headers_for(self.user))

        self.assertEqual(response.status_code, 200)
        results = response.data["results"] if isinstance(response.data, dict) else response.data
        self.assertEqual([item["id"] for item in results], [self.mine.pk])
        self.assertNotIn("access_token", results[0])
        self.assertNotIn("access_token_encrypted", results[0])
        self.assertNotIn("tok-mio", str(response.content))

    def test_desconectar_descarta_el_token_sin_borrar_la_tienda(self):
        response = self.client.post(f"{STORES_URL}{self.mine.pk}/disconnect/", **auth_headers_for(self.user))

        self.assertEqual(response.status_code, 200, response.data)
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.status, StoreConnection.Status.REVOKED)
        self.assertEqual(self.mine.access_token, "")
        self.assertIsNotNone(self.mine.disconnected_at)
        self.assertTrue(AuditLog.objects.filter(action="store.disconnect", actor=self.user).exists())

    def test_no_se_puede_desconectar_una_tienda_ajena(self):
        response = self.client.post(f"{STORES_URL}{self.theirs.pk}/disconnect/", **auth_headers_for(self.user))

        self.assertEqual(response.status_code, 404)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.status, StoreConnection.Status.ACTIVE)

    def test_tienda_sin_vincular_no_recibe_pedidos(self):
        unclaimed = StoreConnection.objects.create(platform="tiendanube", external_store_id="333")
        normalized = get_provider("tiendanube").normalize_order(tiendanube_order())

        with self.assertRaises(ValueError):
            upsert_store_order(unclaimed, normalized)
