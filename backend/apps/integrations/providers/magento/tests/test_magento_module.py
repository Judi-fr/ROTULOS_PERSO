"""Tests de Magento fase 2 (nuestro módulo "Rotulos_Envio"): el worker le
escribe la configuración, el módulo avisa los pedidos guardados, pide el
enlace de impresión y cotiza en el checkout, todo firmado con el secreto de
la tienda. El Magento simulado está en ``fake_magento.py``."""

import base64
import hashlib
import hmac
import io
import json
import time
import zipfile
from decimal import Decimal

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.integrations.events import process_due_events
from apps.integrations.models import IntegrationEvent, ShippingRate
from apps.integrations.providers import get_provider
from apps.integrations.providers.magento.provider import MODULE_ORDER_EVENT
from apps.integrations.stores import enqueue_store_setup
from apps.integrations.tests.helpers import auth_headers_for, make_user
from apps.orders.models import Order

from .fake_magento import MAGENTO_SETTINGS, MagentoTestMixin, magento_order

BASE_URL = "https://rotulos.example.com"
MODULE_SETTINGS = {**MAGENTO_SETTINGS, "INTEGRATIONS_PUBLIC_BASE_URL": BASE_URL}
WEBHOOKS_URL = "/api/v1/integrations/magento/webhooks/"
PRINT_LINK_URL = "/api/v1/integrations/magento/print-link/"
RATES_URL = "/api/v1/integrations/magento/rates/"


class FreshThrottleMixin:
    """Los avisos del módulo llegan sin sesión: el límite anónimo (60/min, en
    la caché) se compartiría con el resto de la suite y frenaría a los tests de
    otras plataformas. Cada test arranca y termina con el contador en cero."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        super().setUp()


def signed(client, url, data, secret, query=""):
    body = json.dumps(data).encode()
    signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return client.generic(
        "POST", f"{url}{query}", body, content_type="application/json", HTTP_X_ROTULOS_SIGNATURE=signature
    )


@override_settings(**MODULE_SETTINGS)
class ModuleConfigTests(FreshThrottleMixin, MagentoTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.connection = self._connection(owner=make_user("comercio@example.com"))

    def test_al_conectar_se_le_escribe_la_configuracion_al_modulo(self):
        self.magento.module_installed = True
        enqueue_store_setup(self.connection)
        process_due_events()

        self.connection.refresh_from_db()
        self.assertEqual(
            self.magento.module_config,
            {
                "storeId": str(self.connection.pk),
                "secret": self.connection.webhook_secret,
                "webhookUrl": f"{BASE_URL}{WEBHOOKS_URL}?store={self.connection.pk}",
                "printLinkUrl": f"{BASE_URL}{PRINT_LINK_URL}",
                "ratesUrl": f"{BASE_URL}{RATES_URL}",
            },
        )
        self.assertIs(self.connection.preferences["print_plugin"], True)
        self.assertEqual(self.connection.preferences["magento_module_version"], "1.0.0")
        self.assertEqual(self.connection.last_error, "")

    def test_sin_el_modulo_la_tienda_funciona_igual(self):
        enqueue_store_setup(self.connection)
        for _ in range(3):
            process_due_events()
        self.connection.refresh_from_db()
        self.assertIs(self.connection.preferences["print_plugin"], False)
        self.assertEqual(self.connection.last_error, "")
        self.assertFalse(IntegrationEvent.objects.filter(connection=self.connection).exclude(status="done").exists())

    def test_sin_el_permiso_del_modulo_no_queda_vinculado(self):
        self.magento.module_installed, self.magento.module_allowed = True, False
        url = f"/api/v1/integrations/stores/{self.connection.pk}/check-print-plugin/"
        response = self.client.post(url, **auth_headers_for(self.connection.owner))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIs(response.data["print_plugin_linked"], False)
        self.connection.refresh_from_db()
        # Un permiso que falta en el módulo no es un problema de la tienda.
        self.assertEqual(self.connection.status, "active")

    def test_verificar_modulo_lo_vincula_en_el_momento(self):
        self.magento.module_installed = True
        url = f"/api/v1/integrations/stores/{self.connection.pk}/check-print-plugin/"
        response = self.client.post(url, **auth_headers_for(self.connection.owner))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIs(response.data["print_plugin_linked"], True)

    @override_settings(INTEGRATIONS_PUBLIC_BASE_URL="")
    def test_sin_url_publica_no_se_marca_error(self):
        enqueue_store_setup(self.connection)
        process_due_events()
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.last_error, "")


@override_settings(**MODULE_SETTINGS)
class ModuleOrderEventTests(FreshThrottleMixin, MagentoTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.connection = self._connection(owner=make_user("comercio@example.com"))
        self.secret = get_provider("magento").ensure_webhook_secret(self.connection)

    def notify(self, data, secret=None, store=None):
        return signed(self.client, WEBHOOKS_URL, data, secret or self.secret, f"?store={store or self.connection.pk}")

    def test_un_pedido_guardado_entra_al_instante(self):
        self.magento.orders = {7: magento_order(7)}
        response = self.notify({"event": "order_saved", "order_id": "7", "updated_at": "2026-10-22 10:00:00"})
        self.assertEqual(response.status_code, 200, response.data)
        event = IntegrationEvent.objects.get(event_type=MODULE_ORDER_EVENT)
        # En la cola queda solo el id: el pedido se vuelve a pedir a la API.
        self.assertEqual(event.payload, {"id": "7", "updated_at": "2026-10-22 10:00:00"})

        process_due_events()
        self.assertTrue(Order.objects.filter(store_connection=self.connection, external_id="7").exists())

    def test_firma_de_otra_tienda_se_rechaza(self):
        response = self.notify({"event": "order_saved", "order_id": "7"}, secret="otro")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(IntegrationEvent.objects.filter(event_type=MODULE_ORDER_EVENT).exists())

    def test_ping_no_encola_nada(self):
        response = self.notify({"event": "ping"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(IntegrationEvent.objects.filter(event_type=MODULE_ORDER_EVENT).exists())

    def test_aviso_sin_id_se_rechaza(self):
        self.assertEqual(self.notify({"event": "order_saved"}).status_code, 400)


@override_settings(**MODULE_SETTINGS)
class ModulePrintTests(FreshThrottleMixin, MagentoTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("comercio@example.com")
        self.connection = self._connection(owner=self.owner)
        self.secret = get_provider("magento").ensure_webhook_secret(self.connection)
        self.magento.orders = {11: magento_order(11), 12: magento_order(12)}

    def link(self, ids, secret=None, ts=None, **extra):
        data = {"store": str(self.connection.pk), "ids": ids, "ts": int(ts if ts is not None else time.time()), **extra}
        return signed(self.client, PRINT_LINK_URL, data, secret or self.secret)

    def test_los_pedidos_elegidos_salen_en_un_pdf(self):
        response = self.link(["12", "11"])
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["count"], response.data["missing"]), (2, []))
        self.assertTrue(response.data["url"].startswith(f"{BASE_URL}/api/v1/integrations/magento/print/"))

        document = self.client.get(response.data["url"].replace(BASE_URL, ""))
        self.assertEqual(document.status_code, 200)
        self.assertTrue(document.content.startswith(b"%PDF"))
        self.assertTrue(AuditLog.objects.filter(action="label.batch", actor=self.owner).exists())

    def test_planilla_de_retiro(self):
        response = self.link(["11"], action="manifest")
        self.assertEqual((response.status_code, response.data["action"]), (200, "manifest"))

    def test_firma_invalida_o_pedido_viejo_se_rechazan(self):
        self.assertEqual(self.link(["11"], secret="otro").status_code, 401)
        self.assertEqual(self.link(["11"], ts=time.time() - 3600).status_code, 401)
        self.assertIn("módulo", self.link(["11"], secret="otro").data["detail"])

    def test_un_enlace_de_magento_no_sirve_en_otra_plataforma(self):
        url = self.link(["11"]).data["url"].replace(BASE_URL, "")
        token = url.rsplit("/", 1)[1]
        self.assertNotEqual(self.client.get(f"/api/v1/integrations/woocommerce/print/{token}").status_code, 200)


@override_settings(**MODULE_SETTINGS)
class ModuleRatesTests(FreshThrottleMixin, MagentoTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.connection = self._connection(owner=make_user("comercio@example.com"))
        self.secret = get_provider("magento").ensure_webhook_secret(self.connection)
        ShippingRate.objects.create(
            connection=self.connection,
            option_code="standard",
            option_name="Estándar",
            postal_code_from="1000",
            postal_code_to="1499",
            weight_up_to_kg=Decimal("5"),
            price=Decimal("3500"),
            delivery_days_min=3,
            delivery_days_max=5,
        )

    def quote(self, secret=None, **cart):
        data = {"store": str(self.connection.pk), "ts": int(time.time()), "postcode": "1426", "country": "AR", "weight_kg": 2, "currency": "ARS"}
        data.update(cart)
        return signed(self.client, RATES_URL, data, secret or self.secret)

    def test_cotiza_con_la_tabla_de_la_tienda(self):
        response = self.quote()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            response.data["rates"],
            [{"code": "standard", "name": "Estándar", "price": "3500.00", "currency": "ARS", "delivery_days_min": 3, "delivery_days_max": 5}],
        )

    def test_fuera_de_la_tabla_no_se_cotiza(self):
        self.assertEqual(self.quote(postcode="5000").data["rates"], [])

    def test_firma_invalida(self):
        response = self.quote(secret="otro")
        self.assertEqual((response.status_code, response.data["rates"]), (401, []))


class ModuleDownloadTests(FreshThrottleMixin, APITestCase):
    def test_el_zip_trae_el_modulo_en_app_code(self):
        response = self.client.get("/api/v1/integrations/magento/module/", **auth_headers_for(make_user("c@example.com")))
        self.assertEqual(response.status_code, 200)
        self.assertIn("rotulos-envio-magento.zip", response["Content-Disposition"])
        names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
        for expected in (
            "app/code/Rotulos/Envio/registration.php",
            "app/code/Rotulos/Envio/etc/module.xml",
            "app/code/Rotulos/Envio/etc/webapi.xml",
            "app/code/Rotulos/Envio/Model/Carrier/Rotulos.php",
            "app/code/Rotulos/Envio/view/adminhtml/ui_component/sales_order_grid.xml",
        ):
            self.assertIn(expected, names)

    def test_pide_sesion(self):
        self.assertEqual(self.client.get("/api/v1/integrations/magento/module/").status_code, 401)
