"""Tests de Correo Argentino por la API común: Paq.ar despacha (alta, rótulo,
seguimiento, cancelación, sucursales) y MiCorreo cotiza. Correo se simula
(``fake_correo.py``)."""

from decimal import Decimal

from rest_framework.test import APITestCase

from apps.carriers.common import province_code
from apps.carriers.correo_argentino.provider import classify
from apps.carriers.models import CarrierAccount, CarrierShipment
from apps.integrations.tests.helpers import auth_headers_for, make_user
from apps.orders.models import Address, Order

from .fake_correo import AGREEMENT, API_KEY, CUSTOMER_ID, MICORREO_PASSWORD, MICORREO_USER, CorreoTestMixin
from apps.accounts.tests.team_helpers import new_business

API = "/api/v1/carriers"


def make_order(user, number="3001", **address_overrides):
    values = {
        "user": user,
        "recipient_name": "Juan Gómez",
        "street": "Colón",
        "number": "200",
        "reference": "Depto 4",
        "city": "Córdoba",
        "state": "Córdoba",
        "postal_code": "5000",
        "origin": Address.Origin.SHIPMENT,
    }
    values.update(address_overrides)
    address = Address.objects.create(**values)
    return Order.objects.create(user=user, address=address, external_number=number, total_weight_kg=Decimal("1.2"))


class ProvinceTests(APITestCase):
    def test_provincia_por_nombre_alias_o_cpa(self):
        self.assertEqual(province_code("Córdoba"), "X")
        self.assertEqual(province_code("CABA"), "C")
        self.assertEqual(province_code("Provincia de Buenos Aires"), "B")
        self.assertEqual(province_code("", "B1842ZAB"), "B")
        self.assertEqual(province_code("Narnia", "5000"), "")


class AccountTests(CorreoTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")
        self.biz = new_business(self.user)

    def test_cuenta_nueva_arranca_con_el_clasico_y_las_claves_no_vuelven(self):
        empty = self.client.get(f"{API}/accounts/correo_argentino/", **auth_headers_for(self.user))
        self.assertEqual([contract["code"] for contract in empty.json()["contracts"]], ["CP-D", "CP-S"])

        response = self.client.put(
            f"{API}/accounts/correo_argentino/",
            {
                "environment": "qa",
                "client_code": AGREEMENT,
                "password": API_KEY,
                "micorreo_user": MICORREO_USER,
                "micorreo_password": MICORREO_PASSWORD,
                "customer_id": CUSTOMER_ID,
                "contracts": [{"code": "cp", "kind": "home"}, {"code": "CP-S", "kind": "branch"}],
                "sender_name": "Mi Tienda SRL",
                "origin_street": "Vicente Lopez",
                "origin_number": "448",
                "origin_postal_code": "1842",
                "origin_city": "Monte Grande",
                "origin_state": "Buenos Aires",
            },
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertNotIn("micorreo_password", data)
        self.assertTrue(data["has_micorreo_password"])
        self.assertEqual([contract["code"] for contract in data["contracts"]], ["CP-D", "CP-S"])
        self.assertTrue(data["ready"], data["missing"])
        self.assertEqual(data["quote_missing"], [])
        account = CarrierAccount.objects.get()
        self.assertEqual(account.secret("micorreo_password"), MICORREO_PASSWORD)

        test = self.client.post(f"{API}/accounts/correo_argentino/test/", **auth_headers_for(self.user))
        self.assertEqual(test.status_code, 200, test.content)
        self.assertIn("MiCorreo también", test.json()["detail"])

    def test_sin_provincia_de_origen_no_esta_lista(self):
        account = self.make_correo_account(self.biz, origin_state="", origin_postal_code="1842")
        response = self.client.get(f"{API}/accounts/correo_argentino/", **auth_headers_for(self.user))
        self.assertFalse(response.json()["ready"])
        self.assertIn("la provincia de origen", response.json()["missing"])
        self.assertTrue(account.pk)


class ShipmentTests(CorreoTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")
        self.biz = new_business(self.user)
        self.account = self.make_correo_account(self.biz)
        self.order = make_order(self.biz)

    def create(self, contract="CP-D", **extra):
        payload = {"carrier": "correo_argentino", "order_ids": [self.order.pk], "contract": contract, **extra}
        return self.client.post(f"{API}/shipments/", payload, format="json", **auth_headers_for(self.user))

    def test_cotiza_con_micorreo(self):
        response = self.client.post(
            f"{API}/shipments/quote/",
            {"carrier": "correo_argentino", "order_ids": [self.order.pk], "contract": "CP-S"},
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["results"][0]["price"], "398.06")
        rates = self.correo.calls_to("POST", "/rates")[0]["json"]
        self.assertEqual(rates["deliveredType"], "S")
        self.assertEqual(rates["dimensions"]["weight"], 1200)
        self.assertEqual(rates["postalCodeOrigin"], "1842")

    def test_sin_micorreo_no_cotiza_pero_despacha(self):
        CarrierAccount.objects.all().delete()
        self.account = self.make_correo_account(self.biz, with_micorreo=False)
        quote = self.client.post(
            f"{API}/shipments/quote/",
            {"carrier": "correo_argentino", "order_ids": [self.order.pk], "contract": "CP-D"},
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertIn("MiCorreo", quote.json()["results"][0]["detail"])
        self.assertTrue(self.create().json()["results"][0]["ok"])
        self.assertIsNone(CarrierShipment.objects.get().quoted_price)

    def test_alta_a_domicilio_con_provincia_y_un_solo_paquete(self):
        response = self.create(package_count=3)
        self.assertTrue(response.json()["results"][0]["ok"], response.content)
        order = self.correo.orders[0]["order"]
        self.assertEqual(order["deliveryType"], "homeDelivery")
        self.assertEqual(order["serviceType"], "CP")
        self.assertEqual(order["shippingData"]["address"]["state"], "X")
        self.assertEqual(order["senderData"]["address"]["state"], "B")
        self.assertEqual(len(order["parcels"]), 1)
        self.assertEqual(order["parcels"][0]["productWeight"], "1200")
        self.assertEqual(order["parcels"][0]["declaredValue"], "15000")
        self.assertEqual(order["shipmentClientId"], "3001")
        shipment = CarrierShipment.objects.get()
        self.assertEqual(shipment.quoted_price, Decimal("498.06"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.carrier, "Correo Argentino")
        self.assertEqual(self.order.tracking_number, shipment.tracking_number)
        self.assertIn(shipment.tracking_number, self.order.tracking_url)

    def test_a_domicilio_sin_provincia_reconocible_no_sale(self):
        other = make_order(self.biz, number="3002", state="", postal_code="5000")
        response = self.client.post(
            f"{API}/shipments/", {"carrier": "correo_argentino", "order_ids": [other.pk], "contract": "CP-D"}, format="json", **auth_headers_for(self.user)
        )
        self.assertFalse(response.json()["results"][0]["ok"])
        self.assertIn("provincia", response.json()["results"][0]["detail"])
        self.assertFalse(self.correo.orders)

    def test_a_sucursal_con_la_sucursal_elegida(self):
        branches = self.client.get(f"{API}/branches/correo_argentino/?cp=5000&state=Córdoba", **auth_headers_for(self.user))
        self.assertEqual(branches.status_code, 200, branches.content)
        self.assertEqual(branches.json()["results"][0]["id"], "CBA")
        self.assertEqual(self.correo.calls_to("GET", "/agencies")[0]["params"]["stateId"], "X")
        response = self.create("CP-S", branches={str(self.order.pk): {"id": "CBA", "name": "CORDOBA CENTRO"}})
        self.assertTrue(response.json()["results"][0]["ok"], response.content)
        order = self.correo.orders[0]["order"]
        self.assertEqual((order["deliveryType"], order["agencyId"]), ("agency", "CBA"))

    def test_rotulo_seguimiento_y_cancelacion(self):
        self.create()
        shipment = CarrierShipment.objects.get()
        label = self.client.get(f"{API}/shipments/{shipment.pk}/label/", **auth_headers_for(self.user))
        self.assertEqual(label.status_code, 200, label.content)
        self.assertTrue(label.content.startswith(b"%PDF"))
        self.assertEqual(self.correo.calls_to("POST", "/labels")[0]["params"], {"labelFormat": "10x15"})

        self.correo.tracking[shipment.tracking_number] = [
            {"facilityCode": "CORREO ARGENTINO", "statusId": "PRE", "status": "PREIMPOSICION", "date": "14-10-2026 10:00", "facility": "CORREO ARGENTINO"},
        ]
        refresh = self.client.post(f"{API}/shipments/{shipment.pk}/refresh/", **auth_headers_for(self.user))
        self.assertEqual(refresh.json()["status"], "pending")
        cancel = self.client.post(f"{API}/shipments/{shipment.pk}/cancel/", **auth_headers_for(self.user))
        self.assertEqual(cancel.status_code, 200, cancel.content)
        self.assertEqual(self.correo.cancelled, [shipment.tracking_number])

    def test_imposicion_y_entrega_mueven_el_pedido(self):
        self.create()
        shipment = CarrierShipment.objects.get()
        self.correo.tracking[shipment.tracking_number] = [
            {"statusId": "IMP", "status": "IMPOSICION", "date": "2026-10-15T09:00:00-03:00", "facility": "CTP"},
            {"statusId": "ENT", "status": "ENTREGADO", "date": "2026-10-17T12:00:00-03:00", "facility": "CORDOBA"},
        ]
        self.client.post(f"{API}/shipments/{shipment.pk}/refresh/", **auth_headers_for(self.user))
        shipment.refresh_from_db()
        self.assertEqual(shipment.status, CarrierShipment.Status.DELIVERED)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.DELIVERED)

    def test_clasificacion_de_estados(self):
        self.assertEqual(classify("CAU", "caduco")[0], CarrierShipment.Status.CANCELLED)
        self.assertEqual(classify("", "DEVUELTO AL REMITENTE")[0], CarrierShipment.Status.RETURNING)
        self.assertEqual(classify("", "EN ESPERA EN SUCURSAL")[0], CarrierShipment.Status.AT_BRANCH)
        self.assertEqual(classify("", "NO ENTREGADO")[0], CarrierShipment.Status.ISSUE)
