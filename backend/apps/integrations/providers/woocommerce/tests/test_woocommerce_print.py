"""Tests de los rótulos impresos desde el admin de WooCommerce (plugin
"Rótulos de envío"): el worker configura el plugin, el plugin pide el enlace
firmando con el secreto de la tienda y el enlace sirve el PDF. La API de la
tienda se simula (``FakeWoo``)."""

import base64
import hashlib
import hmac
import io
import json
import time
import zipfile
from decimal import Decimal

from django.test import override_settings
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.orders.models import Order

from apps.integrations.events import process_due_events
from apps.integrations.models import IntegrationEvent, ShippingRate, StoreConnection
from apps.integrations.providers import get_provider
from apps.integrations.stores import enqueue_store_setup
from apps.integrations.tests.helpers import auth_headers_for, make_user
from .test_woocommerce import BASE_URL, WOO_SETTINGS, WooTestMixin, woo_order

PRINT_LINK_URL = "/api/v1/integrations/woocommerce/print-link/"
RATES_URL = "/api/v1/integrations/woocommerce/rates/"


@override_settings(**WOO_SETTINGS)
class PluginConfigTests(WooTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.connection = self._connection(owner=make_user("comercio@example.com"))

    def test_al_conectar_se_configura_el_plugin(self):
        self.woo.plugin_settings = {}

        enqueue_store_setup(self.connection)
        process_due_events()

        self.connection.refresh_from_db()
        self.assertEqual(
            self.woo.plugin_settings,
            {
                "rotulos_print_link_url": f"{BASE_URL}{PRINT_LINK_URL}",
                "rotulos_rates_url": f"{BASE_URL}{RATES_URL}",
                "rotulos_store_id": str(self.connection.pk),
                "rotulos_secret": self.connection.webhook_secret,
            },
        )
        # El secreto del plugin es el mismo con el que firman los webhooks.
        self.assertTrue(all(hook["secret"] == self.connection.webhook_secret for hook in self.woo.webhooks))

    def test_verificar_plugin_lo_vincula_en_el_momento(self):
        self.woo.plugin_settings = {}
        url = f"/api/v1/integrations/stores/{self.connection.pk}/check-print-plugin/"

        response = self.client.post(url, **auth_headers_for(self.connection.owner))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertIs(response.data["print_plugin_linked"], True)
        self.assertEqual(self.woo.plugin_settings["rotulos_store_id"], str(self.connection.pk))

    def test_verificar_plugin_sin_instalar_avisa_que_no_esta(self):
        url = f"/api/v1/integrations/stores/{self.connection.pk}/check-print-plugin/"
        response = self.client.post(url, **auth_headers_for(self.connection.owner))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIs(response.data["print_plugin_linked"], False)

    def test_verificar_plugin_de_tienda_ajena_no_se_puede(self):
        url = f"/api/v1/integrations/stores/{self.connection.pk}/check-print-plugin/"
        response = self.client.post(url, **auth_headers_for(make_user("otro@example.com")))
        self.assertEqual(response.status_code, 404)

    def test_sin_el_plugin_la_tienda_se_conecta_igual(self):
        enqueue_store_setup(self.connection)
        process_due_events()
        process_due_events()

        self.assertEqual(len(self.woo.webhooks), 2)
        self.assertFalse(IntegrationEvent.objects.filter(connection=self.connection).exclude(status="done").exists())


@override_settings(**WOO_SETTINGS)
class WooCommercePrintTests(WooTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("comercio@example.com")
        self.connection = self._connection(owner=self.owner)
        self.secret = get_provider("woocommerce").ensure_webhook_secret(self.connection)
        self.woo.orders = {101: woo_order(101), 102: woo_order(102)}

    def _link(self, ids, *, secret=None, store=None, ts=None):
        body = json.dumps(
            {"store": str(store or self.connection.pk), "ids": ids, "ts": int(ts if ts is not None else time.time())}
        ).encode()
        signature = base64.b64encode(hmac.new((secret or self.secret).encode(), body, hashlib.sha256).digest()).decode()
        return self.client.generic(
            "POST", PRINT_LINK_URL, body, content_type="application/json", HTTP_X_ROTULOS_SIGNATURE=signature
        )

    def test_pedidos_elegidos_salen_en_un_pdf_en_ese_orden(self):
        response = self._link([102, 101])

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["count"], response.data["missing"]), (2, []))
        self.assertTrue(response.data["url"].startswith(f"{BASE_URL}/api/v1/integrations/woocommerce/print/"))
        # Los que no teníamos se traen de la tienda: el comerciante los eligió.
        self.assertEqual(
            sorted(Order.objects.filter(store_connection=self.connection).values_list("external_id", flat=True)),
            ["101", "102"],
        )

        document = self.client.get(response.data["url"].replace(BASE_URL, ""))
        self.assertEqual(document.status_code, 200)
        self.assertEqual(document["Content-Type"], "application/pdf")
        self.assertTrue(document.content.startswith(b"%PDF"))
        self.assertEqual(document["Cache-Control"], "no-store")
        self.assertTrue(AuditLog.objects.filter(action="label.batch", actor=self.owner).exists())

    def test_pedido_borrado_en_la_tienda_se_saltea(self):
        response = self._link([101, 999])

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["count"], response.data["missing"]), (1, ["999"]))

    def test_firma_con_otro_secreto_se_rechaza(self):
        response = self._link([101], secret="otro-secreto")
        self.assertEqual(response.status_code, 401)

    def test_no_se_puede_imprimir_pedidos_de_otra_tienda_con_su_numero(self):
        other = self._connection(external_store_id="otra.com.ar", store_url="https://otra.com.ar", owner=self.owner)
        response = self._link([101], store=other.pk)
        self.assertEqual(response.status_code, 401)

    def test_pedido_viejo_se_rechaza(self):
        response = self._link([101], ts=time.time() - 3600)
        self.assertEqual(response.status_code, 401)

    def test_tienda_desconectada_no_imprime(self):
        self.connection.status = StoreConnection.Status.REVOKED
        self.connection.save(update_fields=["status"])
        response = self._link([101])
        self.assertEqual(response.status_code, 401)

    def test_ids_invalidos_se_rechazan(self):
        self.assertEqual(self._link([]).status_code, 400)
        self.assertEqual(self._link(["1; DROP"]).status_code, 400)

    def test_enlace_de_shopify_no_sirve_en_la_ruta_de_woocommerce(self):
        response = self._link([101])
        token = response.data["url"].rsplit("/", 1)[-1]
        self.assertEqual(self.client.get(f"/api/v1/integrations/shopify/print/{token}").status_code, 410)


class PluginDownloadTests(APITestCase):
    def test_el_zip_trae_la_carpeta_del_plugin(self):
        response = self.client.get(
            "/api/v1/integrations/woocommerce/print-plugin/", **auth_headers_for(make_user("comercio@example.com"))
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn('filename="rotulos-envio.zip"', response["Content-Disposition"])
        names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
        # WordPress identifica el plugin por su carpeta: todo tiene que ir adentro.
        self.assertIn("rotulos-envio/rotulos-envio.php", names)
        self.assertIn("rotulos-envio/readme.txt", names)
        self.assertIn("rotulos-envio/languages/rotulos-envio-es_AR.mo", names)
        self.assertTrue(all(name.startswith("rotulos-envio/") for name in names))

    def test_sin_sesion_no_se_baja(self):
        self.assertEqual(self.client.get("/api/v1/integrations/woocommerce/print-plugin/").status_code, 401)


@override_settings(**WOO_SETTINGS)
class WooCommerceRatesTests(WooTestMixin, APITestCase):
    """El método de envío del plugin pregunta el precio en el checkout."""

    def setUp(self):
        super().setUp()
        self.connection = self._connection(owner=make_user("comercio@example.com"))
        self.secret = get_provider("woocommerce").ensure_webhook_secret(self.connection)
        for code, name, weight, price in (
            ("standard", "Envío estándar", Decimal("5"), Decimal("4500")),
            ("standard", "Envío estándar", None, Decimal("8000")),
            ("express", "Envío express", Decimal("5"), Decimal("7000")),
        ):
            ShippingRate.objects.create(
                connection=self.connection,
                option_code=code,
                option_name=name,
                postal_code_from="1000",
                postal_code_to="1499",
                weight_up_to_kg=weight,
                price=price,
                delivery_days_min=3,
                delivery_days_max=5,
            )

    def _quote(self, *, secret=None, ts=None, **fields):
        body = {"store": str(self.connection.pk), "ts": int(ts if ts is not None else time.time())}
        body.update({"postcode": "1426", "country": "AR", "weight_kg": 1, "currency": "ARS"})
        body.update(fields)
        raw = json.dumps(body).encode()
        signature = base64.b64encode(hmac.new((secret or self.secret).encode(), raw, hashlib.sha256).digest()).decode()
        return self.client.generic("POST", RATES_URL, raw, content_type="application/json", HTTP_X_ROTULOS_SIGNATURE=signature)

    def test_cotiza_una_tarifa_por_modalidad(self):
        response = self._quote()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(rate["code"], rate["price"]) for rate in response.data["rates"]],
            [("express", "7000.00"), ("standard", "4500.00")],
        )
        self.assertEqual(response.data["rates"][0]["delivery_days_max"], 5)

    def test_carrito_pesado_paga_la_franja_que_le_alcanza(self):
        response = self._quote(weight_kg=6)
        self.assertEqual([(rate["code"], rate["price"]) for rate in response.data["rates"]], [("standard", "8000.00")])

    def test_cpa_se_compara_por_sus_digitos(self):
        response = self._quote(postcode="C1426ABC")
        self.assertEqual(len(response.data["rates"]), 2)

    def test_destino_fuera_de_la_tabla_no_se_cotiza(self):
        response = self._quote(postcode="5000")
        self.assertEqual((response.status_code, response.data["rates"]), (200, []))

    def test_otro_pais_no_se_cotiza(self):
        self.assertEqual(self._quote(country="UY").data["rates"], [])

    def test_peso_invalido_cuenta_como_cero(self):
        response = self._quote(weight_kg="mucho")
        self.assertEqual(len(response.data["rates"]), 2)

    def test_firma_invalida_se_rechaza(self):
        response = self._quote(secret="otro")
        self.assertEqual((response.status_code, response.data["rates"]), (401, []))

    def test_pedido_viejo_se_rechaza(self):
        self.assertEqual(self._quote(ts=time.time() - 3600).status_code, 401)

    def test_un_error_inesperado_no_rompe_el_checkout(self):
        from unittest.mock import patch

        with patch("apps.integrations.providers.woocommerce.rates.matching_rates", side_effect=RuntimeError("boom")):
            response = self._quote()
        self.assertEqual((response.status_code, response.data["rates"]), (200, []))
