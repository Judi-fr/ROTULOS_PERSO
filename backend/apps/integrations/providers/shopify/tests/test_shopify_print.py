"""Tests de los rótulos impresos desde el admin de Shopify (extensión de
impresión): ID token de Shopify, enlace firmado al PDF, pedidos que faltan y
CORS acotado a esa ruta. La API de Shopify se simula (``FakeShopify``)."""

import time

import jwt
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.orders.ingestion import upsert_store_order
from apps.orders.models import Order

from apps.integrations.models import StoreConnection
from apps.integrations.providers import get_provider
from .test_shopify_connection import SECRET, SHOP, SHOPIFY_SETTINGS, ShopifyTestMixin
from .test_shopify_orders import shopify_order
from apps.integrations.tests.helpers import make_user

PRINT_LINK_URL = "/api/v1/integrations/shopify/print-link/"
EXTENSION_ORIGIN = "https://extensions.shopifycdn.com"


def id_token(shop=SHOP, *, secret=SECRET, aud="cliente-shopify", exp_offset=60, dest=None):
    now = int(time.time())
    claims = {
        "iss": f"https://{shop}/admin",
        "dest": dest or f"https://{shop}",
        "aud": aud,
        "sub": "42",
        "exp": now + exp_offset,
        "nbf": now - 5,
        "iat": now - 5,
        "jti": "abc",
        "sid": "s",
    }
    return jwt.encode(claims, secret, algorithm="HS256")


@override_settings(**SHOPIFY_SETTINGS)
class ShopifyPrintTests(ShopifyTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("comercio@example.com")
        self.connection = self._connection(owner=self.owner)
        provider = get_provider("shopify")
        for legacy_id in (101, 102):
            upsert_store_order(self.connection, provider.normalize_order(shopify_order(legacy_id)))

    def _link(self, ids, token=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token or id_token()}"}
        return self.client.post(PRINT_LINK_URL, {"ids": ids}, format="json", **headers)

    def _document(self, url):
        return self.client.get(url.replace("https://rotulos.example.com", ""))

    def test_pedidos_elegidos_salen_en_un_pdf(self):
        response = self._link(["gid://shopify/Order/102", "gid://shopify/Order/101"])

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["count"], response.data["missing"]), (2, []))
        self.assertTrue(response.data["url"].startswith("https://rotulos.example.com/api/v1/integrations/shopify/print/"))

        document = self._document(response.data["url"])
        self.assertEqual(document.status_code, 200)
        self.assertEqual(document["Content-Type"], "application/pdf")
        self.assertTrue(document.content.startswith(b"%PDF"))
        self.assertEqual(document["Cache-Control"], "no-store")
        # La vista previa de Shopify lo muestra en un iframe: sin DENY.
        self.assertNotIn("X-Frame-Options", document)
        self.assertTrue(AuditLog.objects.filter(action="label.batch", actor=self.owner).exists())

    def test_pedido_que_todavia_no_teniamos_se_trae_de_shopify(self):
        self.fake.orders["gid://shopify/Order/555"] = shopify_order(555)

        response = self._link(["gid://shopify/Order/555"])

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(Order.objects.filter(store_connection=self.connection, external_id="555").exists())

    def test_pedido_borrado_en_shopify_se_informa_y_se_imprimen_los_demas(self):
        response = self._link(["gid://shopify/Order/101", "gid://shopify/Order/999"])

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["count"], response.data["missing"]), (1, ["999"]))

    def test_si_no_existe_ninguno_es_un_error(self):
        response = self._link(["gid://shopify/Order/999"])
        self.assertEqual(response.status_code, 400)

    def test_sin_sesion_de_shopify_no_hay_enlace(self):
        self.assertEqual(self.client.post(PRINT_LINK_URL, {"ids": ["1"]}, format="json").status_code, 401)

    def test_token_invalido_no_hay_enlace(self):
        for token in (
            id_token(secret="otro-secreto"),
            id_token(aud="otra-app"),
            id_token(exp_offset=-120),
            id_token(dest="https://otra.myshopify.com"),
        ):
            self.assertEqual(self._link(["gid://shopify/Order/101"], token=token).status_code, 401)

    def test_tienda_no_conectada(self):
        StoreConnection.objects.filter(pk=self.connection.pk).update(status=StoreConnection.Status.REVOKED)
        response = self._link(["gid://shopify/Order/101"])
        self.assertEqual(response.status_code, 400)

    def test_la_otra_tienda_no_imprime_pedidos_de_esta(self):
        other = StoreConnection.objects.create(
            platform="shopify", external_store_id="otra.myshopify.com", access_token="t", owner=make_user("otro@example.com")
        )
        response = self._link(["gid://shopify/Order/101"], token=id_token("otra.myshopify.com"))

        # 101 es de la primera tienda: para "otra" no existe (Shopify dice
        # que no lo tiene) y no sale nada.
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Order.objects.filter(store_connection=other).exists())

    def test_enlace_alterado_no_sirve(self):
        url = self._link(["gid://shopify/Order/101"]).data["url"]
        self.assertEqual(self._document(url + "x").status_code, 410)

    @override_settings(SHOPIFY_PRINT_LINK_MAX_AGE_SECONDS=-1)
    def test_enlace_vencido_no_sirve(self):
        url = self._link(["gid://shopify/Order/101"]).data["url"]
        self.assertEqual(self._document(url).status_code, 410)

    def test_demasiados_pedidos(self):
        ids = [f"gid://shopify/Order/{n}" for n in range(1, 300)]
        self.assertEqual(self._link(ids).status_code, 400)

    # En dev CORS está abierto a todo; esto prueba la configuración de prod.
    @override_settings(CORS_ALLOW_ALL_ORIGINS=False, CORS_ALLOWED_ORIGINS=["https://app.example.com"])
    def test_cors_abierto_solo_para_esta_ruta(self):
        preflight = {
            "HTTP_ORIGIN": EXTENSION_ORIGIN,
            "HTTP_ACCESS_CONTROL_REQUEST_METHOD": "POST",
            "HTTP_ACCESS_CONTROL_REQUEST_HEADERS": "authorization,content-type",
        }
        allowed = self.client.options(PRINT_LINK_URL, **preflight)
        other = self.client.options("/api/v1/orders/", **preflight)

        # La vista previa de impresión baja el PDF con fetch: también preflight.
        document_url = self._link(["gid://shopify/Order/101"]).data["url"].replace("https://rotulos.example.com", "")
        document = self.client.options(document_url, **dict(preflight, HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET"))

        self.assertEqual(allowed["Access-Control-Allow-Origin"], EXTENSION_ORIGIN)
        self.assertEqual(document["Access-Control-Allow-Origin"], EXTENSION_ORIGIN)
        self.assertNotIn("Access-Control-Allow-Origin", other)
