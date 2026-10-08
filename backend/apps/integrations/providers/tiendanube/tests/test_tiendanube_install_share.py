"""Tests del link de instalación para compartir: el comerciante lo genera,
lo abre otra persona (quien administra la tienda) sin sesión nuestra, y la
tienda queda en la cuenta de quien lo generó. La API de Tiendanube se
simula: nunca sale una llamada real."""

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.core import signing
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.integrations.models import StoreConnection
from apps.integrations.stores import STATE_SALT, make_oauth_state, make_share_token
from apps.integrations.tests.helpers import auth_headers_for, fake_response, make_user
from .test_tiendanube_oauth import CALLBACK_URL, FRONTEND, TEST_SETTINGS

SHARE_LINK_URL = "/api/v1/integrations/tiendanube/install-share-link/"
PUBLIC_BASE = "https://rotulos.example.com"
SHARED_PAGE = f"{FRONTEND}/tienda_conectada.html"


def share_open_url(token):
    return f"/api/v1/integrations/tiendanube/install/{token}/"


@override_settings(**TEST_SETTINGS, INTEGRATIONS_PUBLIC_BASE_URL=PUBLIC_BASE)
class InstallShareLinkTests(APITestCase):
    def setUp(self):
        self.user = make_user("comercio@example.com")

    def test_requiere_estar_logueado(self):
        response = self.client.post(SHARE_LINK_URL)
        self.assertEqual(response.status_code, 401)

    def test_devuelve_link_publico_que_lleva_a_la_autorizacion(self):
        response = self.client.post(SHARE_LINK_URL, **auth_headers_for(self.user))

        self.assertEqual(response.status_code, 200, response.data)
        share_url = response.data["share_url"]
        self.assertTrue(share_url.startswith(f"{PUBLIC_BASE}/api/v1/integrations/tiendanube/install/"))
        self.assertEqual(response.data["expires_in_hours"], 72)

        # Quien lo abre no manda credenciales.
        opened = self.client.get(urlparse(share_url).path)
        self.assertEqual(opened.status_code, 302)
        location = opened["Location"]
        self.assertTrue(location.startswith("https://www.tiendanube.com/apps/9999/authorize?"))
        state = signing.loads(parse_qs(urlparse(location).query)["state"][0], salt=STATE_SALT)
        self.assertEqual(state["u"], self.user.pk)
        self.assertEqual(state["x"], 1)

    @override_settings(INTEGRATIONS_PUBLIC_BASE_URL="")
    def test_sin_direccion_publica_devuelve_503(self):
        response = self.client.post(SHARE_LINK_URL, **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 503)

    def test_no_existe_para_plataformas_con_dominio_por_tienda(self):
        response = self.client.post(
            "/api/v1/integrations/shopify/install-share-link/", **auth_headers_for(self.user)
        )
        self.assertEqual(response.status_code, 404)

    def test_link_alterado_va_a_la_pagina_publica_con_error(self):
        response = self.client.get(share_open_url("no-es-un-token"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"{SHARED_PAGE}?store_error=share_link_invalid")

    @override_settings(INTEGRATIONS_INSTALL_SHARE_MAX_AGE_SECONDS=-1)
    def test_link_vencido_va_a_la_pagina_publica_con_error(self):
        response = self.client.get(share_open_url(make_share_token(self.user, "tiendanube")))
        self.assertEqual(response["Location"], f"{SHARED_PAGE}?store_error=share_link_invalid")

    def test_link_de_usuario_desactivado_no_sirve(self):
        token = make_share_token(self.user, "tiendanube")
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.client.get(share_open_url(token))
        self.assertEqual(response["Location"], f"{SHARED_PAGE}?store_error=share_link_invalid")

    def test_link_de_otra_plataforma_no_sirve(self):
        response = self.client.get(share_open_url(make_share_token(self.user, "shopify")))
        self.assertEqual(response["Location"], f"{SHARED_PAGE}?store_error=share_link_invalid")


@override_settings(**TEST_SETTINGS)
class SharedInstallCallbackTests(APITestCase):
    def setUp(self):
        self.user = make_user("comercio@example.com")

    def _callback(self, state, **extra):
        token_response = fake_response(json_data={"access_token": "tok", "user_id": 777, "scope": "read_orders"})
        store_response = fake_response(json_data={"name": {"es": "Tienda del jefe"}})
        with patch("apps.integrations.providers.tiendanube.provider.requests.post", return_value=token_response), patch(
            "apps.integrations.providers.tiendanube.provider.requests.request", return_value=store_response
        ):
            return self.client.get(CALLBACK_URL, {"code": "abc", "state": state, **extra})

    def test_instalacion_compartida_queda_en_la_cuenta_del_link_y_vuelve_a_la_pagina_publica(self):
        response = self._callback(make_oauth_state(self.user, "tiendanube", shared=True))

        self.assertEqual(response.status_code, 302)
        connection = StoreConnection.objects.get(platform="tiendanube", external_store_id="777")
        self.assertEqual(connection.owner, self.user)
        self.assertEqual(response["Location"], f"{SHARED_PAGE}?store_connected={connection.pk}")

    def test_instalacion_normal_sigue_volviendo_a_la_pagina_del_comerciante(self):
        response = self._callback(make_oauth_state(self.user, "tiendanube"))
        connection = StoreConnection.objects.get(platform="tiendanube", external_store_id="777")
        self.assertEqual(response["Location"], f"{FRONTEND}/integraciones.html?store_connected={connection.pk}")

    def test_cancelada_desde_un_link_compartido_vuelve_a_la_pagina_publica(self):
        state = make_oauth_state(self.user, "tiendanube", shared=True)
        response = self.client.get(CALLBACK_URL, {"error": "access_denied", "state": state})
        self.assertEqual(response["Location"], f"{SHARED_PAGE}?store_error=authorization_cancelled")
