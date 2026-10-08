"""Tests de Empretienda: agregar la tienda, leer su planilla de ventas
(columnas, filas por producto, estados) e importarla sin duplicar. Los
encabezados de las planillas de prueba son los más probables, no los reales
(A CONFIRMAR con un archivo exportado de Empretienda)."""

import io
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import Workbook
from rest_framework.test import APITestCase

from apps.integrations.models import StoreConnection
from apps.integrations.providers import get_provider
from apps.integrations.providers.empretienda import spreadsheet
from apps.integrations.tests.helpers import auth_headers_for, make_user
from apps.orders.models import Order

HEADERS = [
    "Número de orden", "Fecha", "Nombre", "Apellido", "Email", "Teléfono", "DNI", "Dirección", "Número",
    "Piso / Depto", "Ciudad", "Provincia", "Código postal", "Método de envío", "Costo de envío",
    "Estado de pago", "Estado de envío", "Producto", "Cantidad", "SKU",
]


def row(order, product="Remera", quantity="1", **overrides):
    values = {
        "Número de orden": order, "Fecha": "08/10/2026", "Nombre": "María", "Apellido": "Gómez",
        "Email": "maria@example.com", "Teléfono": "3514440000", "DNI": "30111222", "Dirección": "Av. Colón",
        "Número": "1234", "Piso / Depto": "3 B", "Ciudad": "Córdoba", "Provincia": "Córdoba",
        "Código postal": "5000", "Método de envío": "Correo Argentino a domicilio", "Costo de envío": "2500",
        "Estado de pago": "Aprobado", "Estado de envío": "Pendiente", "Producto": product,
        "Cantidad": quantity, "SKU": "REM-1",
    }
    values.update(overrides)
    return values


def csv_file(rows, headers=HEADERS, name="ventas.csv"):
    """Como lo guarda Excel en castellano: ``;`` y Latin-1."""
    lines = [";".join(headers)] + [";".join(str(r.get(h, "")) for h in headers) for r in rows]
    return SimpleUploadedFile(name, ("\r\n".join(lines) + "\r\n").encode("latin-1"), content_type="text/csv")


def xlsx_file(rows, headers=HEADERS):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for r in rows:
        sheet.append([r.get(h, "") for h in headers])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return SimpleUploadedFile("ventas.xlsx", buffer.getvalue())


class DetectMappingTests(TestCase):
    def test_reconoce_las_columnas_probables_sin_confundir_numeros(self):
        mapping = spreadsheet.detect_mapping(HEADERS)

        self.assertEqual(mapping["order"], "Número de orden")
        self.assertEqual((mapping["first_name"], mapping["last_name"]), ("Nombre", "Apellido"))
        self.assertEqual((mapping["street"], mapping["number"], mapping["apartment"]), ("Dirección", "Número", "Piso / Depto"))
        self.assertEqual((mapping["city"], mapping["state"], mapping["postal_code"]), ("Ciudad", "Provincia", "Código postal"))
        self.assertEqual(mapping["shipping_method"], "Método de envío")
        self.assertEqual((mapping["shipping_status"], mapping["payment_status"]), ("Estado de envío", "Estado de pago"))
        self.assertEqual((mapping["product"], mapping["quantity"], mapping["sku"]), ("Producto", "Cantidad", "SKU"))
        # Nada del comprador que no vaya en el rótulo.
        self.assertNotIn("Email", mapping.values())
        self.assertNotIn("Teléfono", mapping.values())
        self.assertNotIn("Costo de envío", mapping.values())

    def test_lo_corregido_la_vez_anterior_gana(self):
        mapping = spreadsheet.detect_mapping(HEADERS + ["Barrio"], saved={"apartment": "Barrio", "city": "No existe"})
        self.assertEqual(mapping["apartment"], "Barrio")
        self.assertEqual(mapping["city"], "Ciudad")

    def test_estados(self):
        cases = (
            (("Enviado", "Aprobado", ""), "dispatched"),
            (("En camino", "Aprobado", ""), "in_transit"),
            (("Entregado", "Aprobado", ""), "delivered"),
            (("Pendiente", "Cancelado", ""), "cancelled"),
            (("", "", "Orden cancelada"), "cancelled"),
            (("Pendiente", "Aprobado", ""), ""),
        )
        for values, expected in cases:
            self.assertEqual(spreadsheet._local_status(values), expected, values)


class ConnectTests(APITestCase):
    URL = "/api/v1/integrations/empretienda/connect/"

    def setUp(self):
        self.user = make_user("comercio@example.com")

    def test_agrega_la_tienda_sin_credenciales(self):
        response = self.client.post(
            self.URL, {"store_url": "https://MiTienda.empretienda.com.ar/productos", "name": "Mi Tienda"}, format="json", **auth_headers_for(self.user)
        )
        self.assertEqual(response.status_code, 201, response.data)
        connection = StoreConnection.objects.get(pk=response.data["id"])
        self.assertEqual(
            (connection.platform, connection.external_store_id, connection.store_url, connection.name),
            ("empretienda", "mitienda.empretienda.com.ar", "https://mitienda.empretienda.com.ar", "Mi Tienda"),
        )

    def test_la_misma_tienda_en_otra_cuenta_no(self):
        self.client.post(self.URL, {"store_url": "mitienda.empretienda.com.ar"}, format="json", **auth_headers_for(self.user))
        other = make_user("otro@example.com")
        response = self.client.post(self.URL, {"store_url": "mitienda.empretienda.com.ar"}, format="json", **auth_headers_for(other))
        self.assertEqual(response.status_code, 409)

    def test_direccion_invalida(self):
        response = self.client.post(self.URL, {"store_url": "mitienda"}, format="json", **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(get_provider("empretienda").supports_order_import)


class ImportTests(APITestCase):
    PREVIEW = "/api/v1/integrations/empretienda/import/preview/"
    CONFIRM = "/api/v1/integrations/empretienda/import/confirm/"

    def setUp(self):
        self.user = make_user("comercio@example.com")
        self.store = StoreConnection.objects.create(
            platform="empretienda", external_store_id="mitienda.empretienda.com.ar", store_url="https://mitienda.empretienda.com.ar", owner=self.user
        )

    def _post(self, url, upload, **extra):
        data = {"store": self.store.pk, "file": upload}
        data.update(extra)
        return self.client.post(url, data, format="multipart", **auth_headers_for(self.user))

    def test_vista_previa_agrupa_por_orden_y_no_guarda_nada(self):
        rows = [row("1001", "Remera"), row("1001", "Gorra", "2"), row("1002", Ciudad="")]

        response = self._post(self.PREVIEW, csv_file(rows))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["missing"], [])
        orders = {order["number"]: order for order in response.data["orders"]}
        self.assertEqual((orders["1001"]["items"], orders["1001"]["result"], orders["1001"]["recipient"]), (2, "new", "María Gómez"))
        self.assertEqual(orders["1002"]["result"], "error")
        self.assertIn("Falta el dato de ciudad.", orders["1002"]["errors"])
        self.assertFalse(Order.objects.exists())

    def test_confirmar_importa_y_reimportar_no_duplica(self):
        rows = [row("1001", "Remera"), row("1001", "Gorra", "2"), row("1002", **{"Estado de envío": "Enviado"})]

        response = self._post(self.CONFIRM, xlsx_file(rows))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["created"], response.data["updated"]), (2, 0))
        order = Order.objects.get(store_connection=self.store, external_id="1001")
        self.assertEqual(order.external_number, "1001")
        self.assertEqual(order.description, "1x Remera, 2x Gorra")
        self.assertEqual((order.address.recipient_name, order.address.street, order.address.number), ("María Gómez", "Av. Colón", "1234"))
        self.assertEqual((order.address.reference, order.address.city, order.address.postal_code), ("3 B", "Córdoba", "5000"))
        self.assertEqual(order.shipping_option, "Correo Argentino a domicilio")
        self.assertEqual(order.source, Order.Source.STORE)
        self.assertEqual(Order.objects.get(external_id="1002").status, Order.Status.DISPATCHED)
        # Nada del comprador fuera del rótulo, ni en el pedido ni en lo guardado.
        self.assertEqual((order.contact_email, order.contact_phone), ("", ""))
        stored = json.dumps(order.raw_payload)
        for secret in ("maria@example.com", "3514440000", "30111222"):
            self.assertNotIn(secret, stored)

        # El día siguiente se exporta un rango que se pisa: actualiza.
        again = self._post(self.CONFIRM, csv_file([row("1001", "Remera"), row("1003")]))
        self.assertEqual((again.data["created"], again.data["updated"]), (1, 1))
        self.assertEqual(Order.objects.filter(store_connection=self.store).count(), 3)

    def test_una_planilla_vieja_no_hace_retroceder_el_estado(self):
        self._post(self.CONFIRM, csv_file([row("1001", **{"Estado de envío": "Enviado"})]))
        self._post(self.CONFIRM, csv_file([row("1001")]))
        self.assertEqual(Order.objects.get(external_id="1001").status, Order.Status.DISPATCHED)

    def test_columnas_que_no_reconoce_se_eligen_a_mano_y_se_recuerdan(self):
        headers = ["Orden #", "Cliente", "Domicilio de entrega", "Loc.", "Prod."]
        rows = [{"Orden #": "77", "Cliente": "Juan Pérez", "Domicilio de entrega": "Rivadavia 4500", "Loc.": "CABA", "Prod.": "Taza"}]

        first = self._post(self.PREVIEW, csv_file(rows, headers))
        self.assertIn("Calle", first.data["missing"])
        self.assertEqual(first.data["orders"], [])

        mapping = {"order": "Orden #", "recipient": "Cliente", "street": "Domicilio de entrega", "city": "Loc.", "product": "Prod."}
        confirmed = self._post(self.CONFIRM, csv_file(rows, headers), mapping=json.dumps(mapping))
        self.assertEqual(confirmed.data["created"], 1, confirmed.data)
        order = Order.objects.get(external_id="77")
        self.assertEqual((order.address.street, order.address.number), ("Rivadavia", "4500"))

        # La próxima vez ya sale solo.
        later = self._post(self.PREVIEW, csv_file(rows, headers))
        self.assertEqual(later.data["missing"], [])
        self.assertEqual(later.data["orders"][0]["result"], "update")

    def test_confirmar_sin_columnas_obligatorias(self):
        response = self._post(self.CONFIRM, csv_file([{"X": "1"}], ["X"]))
        self.assertEqual(response.status_code, 400)
        self.assertIn("Número de orden", response.data["detail"])

    def test_tienda_ajena_o_de_otra_plataforma(self):
        other = make_user("otro@example.com")
        foreign = StoreConnection.objects.create(platform="empretienda", external_store_id="otra.com.ar", owner=other)
        woo = StoreConnection.objects.create(platform="woocommerce", external_store_id="woo.com.ar", owner=self.user)
        for store in (foreign, woo):
            response = self.client.post(
                self.PREVIEW, {"store": store.pk, "file": csv_file([row("1")])}, format="multipart", **auth_headers_for(self.user)
            )
            self.assertEqual(response.status_code, 400)
