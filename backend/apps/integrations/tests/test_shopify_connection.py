"""Tests de la conexión de tiendas Shopify: dominio de la tienda, URL de
autorización, callback firmado, apertura de la app desde el admin, token
que vence y se renueva, webhooks (firma, lectura, registro) y
desinstalación. La API de Shopify se simula: nunca sale una llamada real."""

import base64
import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.core import signing
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from ..events import process_due_events
from ..models import IntegrationEvent, StoreConnection
from ..providers import get_provider
from ..providers.base import OAuthResult, ProviderAuthError, ProviderRejectedError
from ..providers.shopify import query_string_hmac
from ..stores import IMPORT_ORDERS_EVENT, STATE_SALT, enqueue_store_setup, make_oauth_state
from ..tokens import access_token_for
from .test_tiendanube_oauth import auth_headers_for, fake_response, make_user

SECRET = "secreto-shopify"
SHOP = "mitienda.myshopify.com"
FRONTEND = "http://localhost:8001"
BASE_URL = "https://rotulos.example.com"

INSTALL_URL = "/api/v1/integrations/shopify/install-url/"
CALLBACK_URL = "/api/v1/integrations/shopify/callback/"
LAUNCH_URL = "/api/v1/integrations/shopify/launch/"
WEBHOOKS_URL = "/api/v1/integrations/shopify/webhooks/"

SHOPIFY_SETTINGS = {
    "SHOPIFY_CLIENT_ID": "cliente-shopify",
    "SHOPIFY_CLIENT_SECRET": SECRET,
    "SHOPIFY_API_VERSION": "2026-07",
    "SHOPIFY_SCOPES": "read_orders",
    "INTEGRATIONS_PUBLIC_BASE_URL": BASE_URL,
    "FRONTEND_URL": FRONTEND,
    "STORE_CONNECT_FRONTEND_PATH": "tiendas.html",
}


def signed(params):
    """Query string firmada como la firma Shopify."""
    return dict(params, hmac=query_string_hmac(params, SECRET))


class FakeShopify:
    """Simula los endpoints de Shopify que usa el proveedor (todos POST)."""

    def __init__(self):
        self.calls = []
        self.token_counter = 0
        self.graphql_status = []  # códigos a devolver en orden antes del 200
        self.refresh_status = 200
        self.webhooks = []
        # Pedidos de la tienda simulada: gid -> nodo GraphQL, y las páginas
        # de la importación (lista de listas de gids) que devuelve orders().
        self.orders = {}
        self.order_pages = [[]]
        # Despacho: los fulfillment orders que ve la app (cada uno con sus
        # envíos ya hechos) y los userErrors que contestan las mutaciones
        # (vacío = aceptada).
        self.fulfillment_orders = []
        self.mutation_errors = []
        # Conexión manual (client credentials): el error de OAuth a devolver,
        # o None para dar el token.
        self.client_credentials_error = None

    def _token(self, prefix):
        self.token_counter += 1
        return {
            "access_token": f"{prefix}-{self.token_counter}",
            "scope": "read_orders",
            "expires_in": 3600,
            "refresh_token": f"shprt-{self.token_counter}",
            "refresh_token_expires_in": 7776000,
        }

    def __call__(self, url, data=None, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "data": data, "json": json, "headers": headers or {}})
        if url.endswith("/admin/oauth/access_token"):
            if data.get("grant_type") == "client_credentials":
                if self.client_credentials_error:
                    return fake_response(400, {"error": self.client_credentials_error})
                self.token_counter += 1
                return fake_response(200, {"access_token": f"propio-{self.token_counter}", "scope": "read_orders", "expires_in": 86399})
            if data.get("grant_type") == "refresh_token":
                if self.refresh_status != 200:
                    return fake_response(self.refresh_status, {"error": "invalid_grant"})
                return fake_response(200, self._token("renovado"))
            return fake_response(200, self._token("tok"))

        if self.graphql_status:
            return fake_response(self.graphql_status.pop(0), {})
        query = json["query"]
        if "ShopInfo" in query:
            return fake_response(
                200,
                {
                    "data": {
                        "shop": {
                            "name": "Mi Tienda",
                            "email": "dueno@example.com",
                            "myshopifyDomain": SHOP,
                            "primaryDomain": {"url": "https://mitienda.com.ar"},
                        }
                    }
                },
            )
        if "webhookSubscriptions" in query:
            return fake_response(200, {"data": {"webhookSubscriptions": {"nodes": self.webhooks}}})
        if "query Orders(" in query:
            after = json["variables"].get("after")
            index = int(after.split("-")[1]) if after else 0
            page = self.order_pages[index]
            has_next = index + 1 < len(self.order_pages)
            return fake_response(
                200,
                {
                    "data": {
                        "orders": {
                            "nodes": [self.orders[gid] for gid in page],
                            "pageInfo": {"hasNextPage": has_next, "endCursor": f"cursor-{index + 1}" if has_next else None},
                        }
                    }
                },
            )
        if "query OrderFulfillment(" in query:
            return fake_response(
                200,
                {
                    "data": {
                        "order": {
                            "fulfillmentOrders": {"nodes": self.fulfillment_orders},
                        }
                    }
                },
            )
        for mutation in ("fulfillmentCreate", "fulfillmentTrackingInfoUpdate"):
            if f"{mutation}(" in query:
                return fake_response(
                    200,
                    {"data": {mutation: {"fulfillment": {"id": "gid://shopify/Fulfillment/1"}, "userErrors": self.mutation_errors}}},
                )
        if "query Order(" in query:
            return fake_response(200, {"data": {"order": self.orders.get(json["variables"]["id"])}})
        if "webhookSubscriptionCreate" in query:
            return fake_response(
                200, {"data": {"webhookSubscriptionCreate": {"webhookSubscription": {"id": "gid://1"}, "userErrors": []}}}
            )
        raise AssertionError(f"Consulta no simulada: {query}")

    def mutations(self, name):
        return [call["json"]["variables"] for call in self.graphql_calls() if f"{name}(" in call["json"]["query"]]

    def graphql_calls(self):
        return [call for call in self.calls if call["url"].endswith("/graphql.json")]

    def token_calls(self):
        return [call for call in self.calls if call["url"].endswith("/admin/oauth/access_token")]


class ShopifyTestMixin:
    def setUp(self):
        super().setUp()
        self.fake = FakeShopify()
        patcher = patch("apps.integrations.providers.shopify.requests.post", side_effect=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _redirect_params(self, response):
        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertTrue(location.startswith(f"{FRONTEND}/tiendas.html"), location)
        return {key: values[0] for key, values in parse_qs(urlparse(location).query).items()}

    def _connection(self, **overrides):
        values = {
            "platform": "shopify",
            "external_store_id": SHOP,
            "access_token": "tok-vigente",
            "refresh_token": "shprt-vigente",
            "token_expires_at": timezone.now() + timedelta(hours=1),
        }
        values.update(overrides)
        return StoreConnection.objects.create(**values)


class ShopDomainTests(TestCase):
    def setUp(self):
        self.provider = get_provider("shopify")

    def test_acepta_lo_que_escribiria_un_comerciante(self):
        for value in ("mitienda", "MiTienda.myshopify.com", "https://mitienda.myshopify.com/admin", " mitienda "):
            self.assertEqual(self.provider.normalize_shop_domain(value), SHOP, value)

    def test_rechaza_dominios_que_no_son_de_shopify(self):
        for value in ("", "mitienda.com", "mitienda.myshopify.com.atacante.com", "-x.myshopify.com", "a b"):
            with self.assertRaises(ValueError, msg=value):
                self.provider.normalize_shop_domain(value)


@override_settings(**SHOPIFY_SETTINGS)
class ShopifyInstallUrlTests(ShopifyTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")

    def test_url_de_la_tienda_con_state_atado_a_usuario_y_tienda(self):
        response = self.client.get(INSTALL_URL, {"shop": "mitienda"}, **auth_headers_for(self.user))

        self.assertEqual(response.status_code, 200, response.data)
        url = urlparse(response.data["authorize_url"])
        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", f"https://{SHOP}/admin/oauth/authorize")
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        self.assertEqual(query["client_id"], "cliente-shopify")
        self.assertEqual(query["scope"], "read_orders")
        self.assertEqual(query["redirect_uri"], f"{BASE_URL}{CALLBACK_URL}")
        state = signing.loads(query["state"], salt=STATE_SALT)
        self.assertEqual((state["u"], state["p"], state["s"]), (self.user.pk, "shopify", SHOP))

    def test_sin_dominio_valido_devuelve_400(self):
        response = self.client.get(INSTALL_URL, {"shop": "mitienda.com"}, **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 400)

    @override_settings(SHOPIFY_CLIENT_SECRET="")
    def test_sin_app_configurada_devuelve_503(self):
        response = self.client.get(INSTALL_URL, {"shop": "mitienda"}, **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 503)


@override_settings(**SHOPIFY_SETTINGS)
class ShopifyCallbackTests(ShopifyTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")

    def _callback(self, state=None, shop=SHOP, **extra):
        params = {"code": "codigo-1", "shop": shop, "timestamp": "1790000000", "host": "YWRtaW4"}
        if state is not None:
            params["state"] = state
        params.update(extra)
        return self.client.get(CALLBACK_URL, signed(params))

    def test_conecta_la_tienda_con_token_que_vence(self):
        before = timezone.now()
        response = self._callback(state=make_oauth_state(self.user, "shopify", SHOP))

        params = self._redirect_params(response)
        connection = StoreConnection.objects.get(platform="shopify", external_store_id=SHOP)
        self.assertEqual(params, {"store_connected": str(connection.pk)})
        self.assertEqual(connection.owner, self.user)
        self.assertEqual(connection.access_token, "tok-1")
        self.assertEqual(connection.refresh_token, "shprt-1")
        self.assertGreaterEqual(connection.token_expires_at, before + timedelta(seconds=3590))
        self.assertGreaterEqual(connection.refresh_token_expires_at, before + timedelta(days=89))
        self.assertEqual(connection.name, "Mi Tienda")
        self.assertEqual(connection.store_url, "https://mitienda.com.ar")

        exchange = self.fake.token_calls()[0]
        self.assertEqual(exchange["url"], f"https://{SHOP}/admin/oauth/access_token")
        self.assertEqual(exchange["data"]["expiring"], "1")
        self.assertEqual(exchange["data"]["code"], "codigo-1")
        self.assertEqual(self.fake.graphql_calls()[0]["headers"]["X-Shopify-Access-Token"], "tok-1")

    def test_callback_con_firma_alterada_no_conecta(self):
        params = signed({"code": "c", "shop": SHOP, "state": make_oauth_state(self.user, "shopify", SHOP)})
        params["code"] = "otro-codigo"
        response = self.client.get(CALLBACK_URL, params)

        self.assertEqual(self._redirect_params(response), {"store_error": "invalid_signature"})
        self.assertFalse(StoreConnection.objects.exists())

    def test_callback_sin_state_no_conecta(self):
        response = self._callback()
        self.assertEqual(self._redirect_params(response), {"store_error": "invalid_state"})
        self.assertFalse(StoreConnection.objects.exists())

    def test_state_de_otra_tienda_no_sirve(self):
        response = self._callback(state=make_oauth_state(self.user, "shopify", "otra.myshopify.com"))
        self.assertEqual(self._redirect_params(response), {"store_error": "invalid_state"})

    def test_state_de_otra_plataforma_no_sirve(self):
        response = self._callback(state=make_oauth_state(self.user, "tiendanube"))
        self.assertEqual(self._redirect_params(response), {"store_error": "invalid_state"})

    def test_dominio_invalido_aunque_venga_firmado(self):
        response = self._callback(state="x", shop="mitienda.myshopify.com.atacante.com")
        self.assertEqual(self._redirect_params(response), {"store_error": "invalid_shop"})

    def test_state_sin_usuario_deja_la_tienda_para_reclamar(self):
        response = self._callback(state=make_oauth_state(None, "shopify", SHOP))

        params = self._redirect_params(response)
        self.assertIn("store_claim", params)
        self.assertIsNone(StoreConnection.objects.get(external_store_id=SHOP).owner)

    def test_al_configurar_registra_pedidos_y_desinstalacion_e_importa(self):
        self._callback(state=make_oauth_state(self.user, "shopify", SHOP))

        process_due_events()

        creates = [call for call in self.fake.graphql_calls() if "webhookSubscriptionCreate" in call["json"]["query"]]
        self.assertEqual(
            [call["json"]["variables"]["topic"] for call in creates],
            ["ORDERS_CREATE", "ORDERS_UPDATED", "ORDERS_CANCELLED", "APP_UNINSTALLED"],
        )
        self.assertEqual(
            {call["json"]["variables"]["webhookSubscription"]["uri"] for call in creates}, {f"{BASE_URL}{WEBHOOKS_URL}"}
        )
        self.assertTrue(IntegrationEvent.objects.filter(event_type=IMPORT_ORDERS_EVENT).exists())


@override_settings(**SHOPIFY_SETTINGS)
class ShopifyLaunchTests(ShopifyTestMixin, APITestCase):
    def test_tienda_nueva_arranca_el_oauth_sin_usuario(self):
        response = self.client.get(LAUNCH_URL, signed({"shop": SHOP, "timestamp": "1790000000"}))

        self.assertEqual(response.status_code, 302)
        url = urlparse(response["Location"])
        self.assertEqual(url.netloc, SHOP)
        state = signing.loads(parse_qs(url.query)["state"][0], salt=STATE_SALT)
        self.assertEqual((state["u"], state["s"]), (None, SHOP))

    def test_tienda_ya_conectada_va_directo_a_nuestra_web(self):
        self._connection(owner=make_user("comercio@example.com"))
        response = self.client.get(LAUNCH_URL, signed({"shop": SHOP, "timestamp": "1790000000"}))
        self.assertEqual(response["Location"], f"{FRONTEND}/tiendas.html")

    def test_tienda_conectada_sin_dueno_va_a_vincularse(self):
        self._connection()
        response = self.client.get(LAUNCH_URL, signed({"shop": SHOP, "timestamp": "1790000000"}))
        self.assertIn("store_claim", self._redirect_params(response))

    def test_tienda_desconectada_vuelve_a_pasar_por_el_oauth(self):
        self._connection(status=StoreConnection.Status.REVOKED)
        response = self.client.get(LAUNCH_URL, signed({"shop": SHOP, "timestamp": "1790000000"}))
        self.assertEqual(urlparse(response["Location"]).netloc, SHOP)

    def test_sin_firma_no_hace_nada(self):
        response = self.client.get(LAUNCH_URL, {"shop": SHOP})
        self.assertEqual(self._redirect_params(response), {"store_error": "invalid_signature"})


@override_settings(**SHOPIFY_SETTINGS)
class ShopifyTokenRefreshTests(ShopifyTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.provider = get_provider("shopify")

    def test_token_vigente_no_se_renueva(self):
        connection = self._connection()
        self.provider.get_store_info(connection)

        self.assertEqual(self.fake.token_calls(), [])
        self.assertEqual(self.fake.graphql_calls()[0]["headers"]["X-Shopify-Access-Token"], "tok-vigente")

    def test_token_por_vencer_se_renueva_antes_y_guarda_el_refresh_nuevo(self):
        connection = self._connection(token_expires_at=timezone.now() + timedelta(seconds=30))

        self.provider.get_store_info(connection)

        refresh = self.fake.token_calls()[0]["data"]
        self.assertEqual(refresh["grant_type"], "refresh_token")
        self.assertEqual(refresh["refresh_token"], "shprt-vigente")
        self.assertEqual(self.fake.graphql_calls()[0]["headers"]["X-Shopify-Access-Token"], "renovado-1")
        connection.refresh_from_db()
        self.assertEqual(connection.access_token, "renovado-1")
        # El refresh token rota: el viejo ya no sirve.
        self.assertEqual(connection.refresh_token, "shprt-1")

    def test_401_renueva_una_vez_y_reintenta(self):
        connection = self._connection()
        self.fake.graphql_status = [401]

        info = self.provider.get_store_info(connection)

        self.assertEqual(info.name, "Mi Tienda")
        self.assertEqual(len(self.fake.token_calls()), 1)
        self.assertEqual(self.fake.graphql_calls()[-1]["headers"]["X-Shopify-Access-Token"], "renovado-1")

    def test_refresh_rechazado_es_error_de_autenticacion(self):
        connection = self._connection(token_expires_at=timezone.now() - timedelta(minutes=5))
        self.fake.refresh_status = 401

        with self.assertRaises(ProviderAuthError):
            self.provider.get_store_info(connection)

    def test_campo_sin_scope_no_es_un_token_invalido(self):
        connection = self._connection()
        denied = fake_response(
            200, {"errors": [{"message": "Access denied for draftOrders field.", "extensions": {"code": "ACCESS_DENIED"}}]}
        )

        with patch("apps.integrations.providers.shopify.requests.post", return_value=denied):
            with self.assertRaises(ProviderRejectedError):
                self.provider.graphql(connection, "{ draftOrders(first: 1) { nodes { id } } }")

    def test_si_otro_proceso_ya_lo_renovo_usa_ese(self):
        connection = self._connection(token_expires_at=timezone.now() + timedelta(seconds=30))
        # Otro proceso lo renovó en la base mientras este tenía la copia vieja.
        other = StoreConnection.objects.get(pk=connection.pk)
        other.set_tokens(OAuthResult(access_token="de-otro", external_store_id=SHOP, refresh_token="r", expires_in=3600))
        other.save()

        self.assertEqual(access_token_for(connection, self.provider), "de-otro")
        self.assertEqual(self.fake.token_calls(), [])

    def test_token_sin_vencimiento_no_se_toca(self):
        connection = StoreConnection.objects.create(platform="tiendanube", external_store_id="1", access_token="fijo")
        self.assertEqual(access_token_for(connection, get_provider("tiendanube"), force_refresh=True), "fijo")


@override_settings(**SHOPIFY_SETTINGS)
class ShopifyWebhookTests(ShopifyTestMixin, APITestCase):
    def _post(self, topic, payload, *, shop=SHOP, secret=SECRET):
        body = json.dumps(payload).encode("utf-8")
        signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
        return self.client.generic(
            "POST",
            WEBHOOKS_URL,
            body,
            content_type="application/json",
            HTTP_X_SHOPIFY_HMAC_SHA256=signature,
            HTTP_X_SHOPIFY_TOPIC=topic,
            HTTP_X_SHOPIFY_SHOP_DOMAIN=shop,
        )

    def test_desinstalar_desconecta_y_descarta_los_tokens(self):
        connection = self._connection(owner=make_user("comercio@example.com"))

        response = self._post("app/uninstalled", {"id": 1, "domain": SHOP})
        process_due_events()

        self.assertEqual(response.status_code, 200)
        event = IntegrationEvent.objects.get(event_type="app/uninstalled")
        self.assertEqual((event.platform, event.connection_id), ("shopify", connection.pk))
        connection.refresh_from_db()
        self.assertEqual(connection.status, StoreConnection.Status.REVOKED)
        self.assertEqual((connection.access_token, connection.refresh_token), ("", ""))
        self.assertIsNone(connection.token_expires_at)

    def test_firma_invalida_devuelve_401(self):
        self.assertEqual(self._post("app/uninstalled", {"id": 1}, secret="otro").status_code, 401)
        self.assertFalse(IntegrationEvent.objects.exists())

    def test_sin_headers_de_tienda_devuelve_400(self):
        self.assertEqual(self._post("app/uninstalled", {"id": 1}, shop="").status_code, 400)

    def test_borrado_de_la_tienda_se_procesa_con_el_handler_de_privacidad(self):
        connection = self._connection()

        self._post("shop/redact", {"shop_id": 1, "shop_domain": SHOP})
        process_due_events()

        event = IntegrationEvent.objects.get(event_type="shop/redact")
        self.assertEqual(event.status, IntegrationEvent.Status.DONE)
        connection.refresh_from_db()
        self.assertEqual(connection.status, StoreConnection.Status.REVOKED)

    def test_la_tienda_de_otra_plataforma_con_el_mismo_id_no_se_mezcla(self):
        StoreConnection.objects.create(platform="tiendanube", external_store_id=SHOP, access_token="t")
        self._post("app/uninstalled", {"id": 1})
        self.assertIsNone(IntegrationEvent.objects.get().connection)


@override_settings(**SHOPIFY_SETTINGS)
class ShopifySetupTests(ShopifyTestMixin, TestCase):
    def test_no_duplica_un_webhook_ya_registrado(self):
        connection = self._connection()
        self.fake.webhooks = [{"id": "gid://1", "topic": "APP_UNINSTALLED", "uri": f"{BASE_URL}{WEBHOOKS_URL}"}]

        enqueue_store_setup(connection)
        process_due_events()

        topics = [
            call["json"]["variables"]["topic"]
            for call in self.fake.graphql_calls()
            if "webhookSubscriptionCreate" in call["json"]["query"]
        ]
        self.assertEqual(topics, ["ORDERS_CREATE", "ORDERS_UPDATED", "ORDERS_CANCELLED"])
