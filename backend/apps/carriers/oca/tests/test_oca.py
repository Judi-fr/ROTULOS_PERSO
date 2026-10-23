"""Tests de OCA por la API común de transportistas: la cuenta (probar la conexión
trae las operativas), cotizar, crear el envío (orden de retiro + número de
envío como seguimiento), etiquetas PDF/ZPL, seguimiento y anulación. OCA se
simula (``fake_oca.py``) con las formas de su QA."""

import xml.etree.ElementTree as ET
from decimal import Decimal

from rest_framework.test import APITestCase

from apps.carriers.models import CarrierShipment
from apps.carriers.oca.provider import OcaProvider, fit, format_cuit, oca_province, split_name
from apps.carriers.tracking import sync_due_shipments
from apps.integrations.tests.helpers import auth_headers_for, make_user
from apps.orders.models import Address, Order

from .fake_oca import PASSWORD, USER, OcaTestMixin
from apps.accounts.tests.team_helpers import give_solo_teams

API = "/api/v1/carriers"


def make_order(user, number="2001", **address_overrides):
    values = {
        "user": user,
        "recipient_name": "María José Pérez",
        "street": "Av. Colón",
        "number": "1234",
        "reference": "Piso 3 B",
        "city": "Córdoba",
        "state": "Córdoba",
        "postal_code": "X5000ABC",
        "origin": Address.Origin.SHIPMENT,
    }
    values.update(address_overrides)
    address = Address.objects.create(**values)
    return Order.objects.create(
        user=user, address=address, external_number=number, total_weight_kg=Decimal("2.5"), contact_phone="3514445555"
    )


class HelpersTests(APITestCase):
    def test_textos_como_los_quiere_oca(self):
        self.assertEqual(format_cuit("30536259194"), "30-53625919-4")
        self.assertEqual(split_name("María José Pérez"), ("María José", "Pérez"))
        self.assertEqual(oca_province("Ciudad Autónoma de Buenos Aires"), "CAPITAL FEDERAL")
        self.assertEqual(oca_province("", "X5000ABC"), "CORDOBA")
        # Bytes, no caracteres: una tilde ocupa dos.
        self.assertEqual(len(fit("ó" * 20, 30).encode()), 30)


class AccountTests(OcaTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")
        give_solo_teams()  # comercio "solo" (team_helpers, transitorio)

    def test_cargar_la_cuenta_y_probar_trae_las_operativas(self):
        response = self.client.put(
            f"{API}/accounts/oca/",
            {
                "environment": "qa",
                "username": USER,
                "password": PASSWORD,
                "client_code": "111757/001",
                "pickup_slot": "2",
                "sender_name": "Mi Tienda SRL",
                "origin_street": "La Rioja",
                "origin_number": "300",
                "origin_postal_code": "1215",
                "origin_city": "CABA",
            },
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn("password", response.json())
        self.assertTrue(response.json()["has_password"])
        self.assertEqual(response.json()["pickup_slot"], "2")
        self.assertIn("al menos una operativa", " ".join(response.json()["missing"]))

        test = self.client.post(f"{API}/accounts/oca/test/", **auth_headers_for(self.user))
        self.assertEqual(test.status_code, 200, test.content)
        codes = {contract["code"]: contract for contract in test.json()["contracts"]}
        self.assertEqual(codes["62342"]["kind"], "branch")
        self.assertEqual(codes["94584"]["origin"], "branch")

    def test_franja_invalida_y_clave_rechazada(self):
        bad = self.client.put(f"{API}/accounts/oca/", {"pickup_slot": "9"}, format="json", **auth_headers_for(self.user))
        self.assertEqual(bad.status_code, 400)
        self.client.put(f"{API}/accounts/oca/", {"environment": "qa", "username": USER, "password": "otra"}, format="json", **auth_headers_for(self.user))
        test = self.client.post(f"{API}/accounts/oca/test/", **auth_headers_for(self.user))
        self.assertEqual(test.status_code, 400)
        self.assertIn("rechazó", test.json()["detail"])

    def test_el_catalogo_lista_los_tres_transportistas(self):
        response = self.client.get(f"{API}/", **auth_headers_for(self.user))
        self.assertEqual([item["code"] for item in response.json()["results"]], ["andreani", "correo_argentino", "oca"])
        oca = response.json()["results"][2]
        self.assertTrue(oca["contracts_from_test"])
        self.assertFalse(oca["account"]["exists"])


class ShipmentTests(OcaTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")
        self.account = self.make_oca_account(self.user)
        self.order = make_order(self.user)
        give_solo_teams()  # comercio "solo" (team_helpers, transitorio)

    def create(self, contract="64665", **extra):
        payload = {"carrier": "oca", "order_ids": [self.order.pk], "contract": contract, **extra}
        return self.client.post(f"{API}/shipments/", payload, format="json", **auth_headers_for(self.user))

    def test_cotiza_con_cuit_operativa_peso_y_volumen(self):
        response = self.client.post(
            f"{API}/shipments/quote/",
            {"carrier": "oca", "order_ids": [self.order.pk], "contract": "64665", "package_count": 2},
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["results"][0]["price"], "591.00")
        self.assertEqual(response.json()["results"][0]["delivery_days"], 3)
        call = self.oca.calls_to("Tarifar_Envio_Corporativo")[0]["data"]
        self.assertEqual(call["Cuit"], "30-53625919-4")
        self.assertEqual(call["CodigoPostalDestino"], "5000")
        self.assertEqual(call["CantidadPaquetes"], "2")
        self.assertEqual(call["PesoTotal"], "2.500")

    def test_cada_pedido_cotiza_con_sus_propios_bultos(self):
        other = make_order(self.user, number="2002")
        response = self.client.post(
            f"{API}/shipments/quote/",
            {
                "carrier": "oca",
                "order_ids": [self.order.pk, other.pk],
                "contract": "64665",
                "package_count": 1,
                "packages": {str(other.pk): 3},
            },
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 200, response.content)
        calls = self.oca.calls_to("Tarifar_Envio_Corporativo")
        self.assertEqual([call["data"]["CantidadPaquetes"] for call in calls], ["1", "3"])

    def test_bultos_por_pedido_fuera_de_rango_se_acotan(self):
        response = self.client.post(
            f"{API}/shipments/quote/",
            {"carrier": "oca", "order_ids": [self.order.pk], "contract": "64665", "packages": {str(self.order.pk): 99}},
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.oca.calls_to("Tarifar_Envio_Corporativo")[0]["data"]["CantidadPaquetes"], "20")

    def test_crear_envio_deja_el_numero_de_envio_como_seguimiento(self):
        response = self.create()
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()["results"][0]
        self.assertTrue(result["ok"], result)
        shipment = CarrierShipment.objects.get()
        self.assertEqual(shipment.carrier, "oca")
        self.assertEqual(shipment.group_number, str(self.oca.next_order))
        self.assertEqual(shipment.quoted_price, Decimal("591.00"))
        self.assertNotIn("QR", shipment.raw_response["detail"])
        self.order.refresh_from_db()
        self.assertEqual(self.order.carrier, "OCA")
        self.assertEqual(self.order.tracking_number, shipment.tracking_number)
        self.assertEqual(self.order.status, Order.Status.PREPARING)

        root = ET.fromstring(self.oca.orders[0].split("?>", 1)[1])
        self.assertEqual(root.find("cabecera").get("nrocuenta"), "111757/001")
        recipient = root.find(".//destinatario")
        self.assertEqual((recipient.get("nombre"), recipient.get("apellido")), ("María José", "Pérez"))
        self.assertEqual(recipient.get("provincia"), "CORDOBA")
        self.assertEqual(recipient.get("cp"), "5000")
        self.assertEqual(recipient.get("observaciones"), "Piso 3 B")
        self.assertEqual(root.find(".//origen").get("provincia"), "CAPITAL FEDERAL")

    def test_a_sucursal_pide_la_sucursal_y_la_manda_como_idci(self):
        response = self.create("62342")
        self.assertFalse(response.json()["results"][0]["ok"])
        self.assertIn("sucursal", response.json()["results"][0]["detail"])
        response = self.create("62342", branches={str(self.order.pk): {"id": "41", "name": "CORDOBA II"}})
        self.assertTrue(response.json()["results"][0]["ok"], response.content)
        root = ET.fromstring(self.oca.orders[0].split("?>", 1)[1])
        self.assertEqual(root.find(".//destinatario").get("idci"), "41")
        self.assertEqual(CarrierShipment.objects.get().delivery_kind, "branch")

    def test_sucursales_solo_las_que_entregan(self):
        response = self.client.get(f"{API}/branches/oca/?cp=X5000ABC", **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([branch["id"] for branch in response.json()["results"]], ["41"])
        self.assertEqual(self.oca.calls_to("GetCentrosImposicionConServiciosByCP")[0]["data"]["CodigoPostal"], "5000")

    def test_etiquetas_pdf_zpl_y_el_pdf_con_el_rotulo(self):
        self.create()
        shipment = CarrierShipment.objects.get()
        pdf = self.client.get(f"{API}/shipments/{shipment.pk}/label/", **auth_headers_for(self.user))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        zpl = self.client.get(f"{API}/shipments/{shipment.pk}/label/?file_type=zpl", **auth_headers_for(self.user))
        self.assertIn(shipment.tracking_number.encode(), zpl.content)
        call = self.oca.calls_to("GetPdfDeEtiquetasPorOrdenOrNumeroEnvioParaEtiquetadora")[0]["data"]
        self.assertEqual(call["ordenRetiro"], shipment.group_number)

    def test_seguimiento_y_anulacion(self):
        self.create()
        shipment = CarrierShipment.objects.get()
        number = shipment.tracking_number
        self.oca.traces[number] = [
            {"NumeroEnvio": number, "Estado": "En proceso de Retiro - Suc: PLANTA", "IdEstado": "1", "Motivo": "Sin Motivo", "Fecha": "2026-10-14T11:33:24.227-03:00"},
        ]
        self.assertEqual(sync_due_shipments(), 0)
        shipment.refresh_from_db()
        self.assertEqual(shipment.status, CarrierShipment.Status.PENDING)

        cancel = self.client.post(f"{API}/shipments/{shipment.pk}/cancel/", **auth_headers_for(self.user))
        self.assertEqual(cancel.status_code, 200, cancel.content)
        self.assertEqual(self.oca.cancelled, [shipment.group_number])
        self.order.refresh_from_db()
        self.assertEqual(self.order.tracking_number, "")

    def test_estados_hasta_entregado(self):
        self.create()
        shipment = CarrierShipment.objects.get()
        number = shipment.tracking_number
        self.oca.traces[number] = [
            {"Estado": "Retirado - Suc: PLANTA", "IdEstado": "3", "Motivo": "Sin Motivo", "Fecha": "2026-10-15T10:00:00-03:00"},
            {"Estado": "En distribución", "IdEstado": "8", "Motivo": "Sin Motivo", "Fecha": "2026-10-16T09:00:00-03:00"},
            {"Estado": "Entregado", "IdEstado": "10", "Motivo": "Sin Motivo", "Fecha": "2026-10-16T15:00:00-03:00"},
        ]
        refresh = self.client.post(f"{API}/shipments/{shipment.pk}/refresh/", **auth_headers_for(self.user))
        self.assertEqual(refresh.status_code, 200, refresh.content)
        self.assertEqual(refresh.json()["status"], "delivered")
        self.assertEqual(len(refresh.json()["events"]), 3)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.DELIVERED)

    def test_clasifica_devolucion_sin_entregar_al_comprador(self):
        provider = OcaProvider()
        from apps.carriers.oca.provider import classify

        self.assertEqual(classify("20", "Devuelto al remitente")[0], CarrierShipment.Status.RETURNING)
        self.assertEqual(classify("54", "Envío Cancelado")[0], CarrierShipment.Status.CANCELLED)
        self.assertEqual(classify("7", "No entregado - domicilio cerrado")[0], CarrierShipment.Status.ISSUE)
        self.assertTrue(provider.tracking_url("123").endswith("123"))

    def test_un_pedido_ajeno_no_se_despacha(self):
        other = make_order(make_user("otro@example.com"), number="9999")
        response = self.client.post(
            f"{API}/shipments/", {"carrier": "oca", "order_ids": [other.pk], "contract": "64665"}, format="json", **auth_headers_for(self.user)
        )
        self.assertFalse(response.json()["results"][0]["ok"])
        self.assertFalse(self.oca.orders)
