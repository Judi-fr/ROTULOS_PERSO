"""Tests de "Imprimir rótulos" en las acciones masivas de Ventas del admin de
Tiendanube: un link de app abre imprimir_tiendanube.html, que llama a
``tiendanube/print-link/`` con el JWT del comerciante. La API de Tiendanube
se simula: nunca sale una llamada real."""

from unittest.mock import patch
from urllib.parse import urlparse

from django.test import override_settings
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.orders.models import Order

from apps.integrations.models import StoreConnection
from apps.integrations.providers.base import ProviderNotFoundError
from apps.integrations.tests.test_store_integration_base import tiendanube_order
from apps.integrations.tests.helpers import auth_headers_for, make_user

PRINT_LINK_URL = "/api/v1/integrations/tiendanube/print-link/"
BASE_URL = "https://rotulos.example.com"


@override_settings(INTEGRATIONS_PUBLIC_BASE_URL=BASE_URL)
class TiendanubePrintLinkTests(APITestCase):
    def setUp(self):
        self.owner = make_user("comercio@example.com")
        self.connection = StoreConnection.objects.create(
            platform="tiendanube", external_store_id="989346", owner=self.owner, access_token="tok"
        )
        self.remote = {"501": tiendanube_order(id=501, number=1501), "502": tiendanube_order(id=502, number=1502)}
        patcher = patch(
            "apps.integrations.providers.tiendanube.provider.TiendanubeProvider.get_order", side_effect=self._get_order
        )
        self.get_order = patcher.start()
        self.addCleanup(patcher.stop)

    def _get_order(self, connection, order_id):
        if str(order_id) not in self.remote:
            raise ProviderNotFoundError("no existe")
        return self.remote[str(order_id)]

    def _link(self, data, user=None):
        return self.client.post(PRINT_LINK_URL, data, format="json", **auth_headers_for(user or self.owner))

    def test_requiere_estar_logueado(self):
        response = self.client.post(PRINT_LINK_URL, {"store": "989346", "ids": [501]}, format="json")
        self.assertEqual(response.status_code, 401)

    def test_pedidos_elegidos_salen_en_un_pdf_en_ese_orden(self):
        response = self._link({"store": "989346", "ids": ["502", "501"]})

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["count"], response.data["missing"]), (2, []))
        self.assertTrue(response.data["url"].startswith(f"{BASE_URL}/api/v1/integrations/tiendanube/print/"))
        # Los que no teníamos se traen de la tienda: el comerciante los eligió.
        self.assertEqual(
            sorted(Order.objects.filter(store_connection=self.connection).values_list("external_id", flat=True)),
            ["501", "502"],
        )
        self.assertTrue(AuditLog.objects.filter(action="label.batch", actor=self.owner).exists())

        pdf = self.client.get(urlparse(response.data["url"]).path)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertEqual(pdf["Cache-Control"], "no-store")

    def test_sin_store_usa_la_unica_tiendanube_del_usuario(self):
        response = self._link({"ids": [501]})
        self.assertEqual(response.status_code, 200, response.data)

    def test_sin_store_y_con_dos_tiendanube_no_adivina(self):
        StoreConnection.objects.create(platform="tiendanube", external_store_id="111", owner=self.owner)
        response = self._link({"ids": [501]})
        self.assertEqual(response.status_code, 404)

    def test_tienda_de_otro_usuario_no_se_imprime(self):
        response = self._link({"store": "989346", "ids": [501]}, user=make_user("otro@example.com"))
        self.assertEqual(response.status_code, 404)
        self.get_order.assert_not_called()

    def test_tienda_desconectada_no_se_imprime(self):
        self.connection.status = StoreConnection.Status.REVOKED
        self.connection.save(update_fields=["status"])
        response = self._link({"store": "989346", "ids": [501]})
        self.assertEqual(response.status_code, 404)

    def test_pedido_borrado_en_la_tienda_se_informa_y_los_demas_se_imprimen(self):
        response = self._link({"store": "989346", "ids": [501, 999]})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["count"], response.data["missing"]), (1, ["999"]))

    def test_ids_invalidos_dan_400(self):
        for ids in ([], ["abc"], "501"):
            response = self._link({"store": "989346", "ids": ids})
            self.assertEqual(response.status_code, 400, ids)

    def test_enlace_de_tiendanube_no_sirve_en_la_ruta_de_otra_plataforma(self):
        url = self._link({"store": "989346", "ids": [501]}).data["url"]
        token = urlparse(url).path.rsplit("/", 1)[-1]
        response = self.client.get(f"/api/v1/integrations/woocommerce/print/{token}")
        self.assertEqual(response.status_code, 410)
