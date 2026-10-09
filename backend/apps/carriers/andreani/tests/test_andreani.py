"""Tests de Andreani: la cuenta del cliente, crear envíos (el número de Andreani
queda como seguimiento y en el rótulo), sucursales y puntos HOP, etiquetas,
seguimiento automático y cancelación. Andreani se simula (``fake_andreani.py``)."""

import io
import zipfile
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.carriers.andreani import shipments as service
from apps.carriers.models import CarrierAccount, CarrierShipment, CarrierShipmentEvent
from apps.carriers.tracking import sync_due_shipments
from apps.integrations.tests.helpers import auth_headers_for, make_user
from apps.labels.label_rendering import build_label_context
from apps.orders.models import Address, Order

from .fake_andreani import PASSWORD, USER, AndreaniTestMixin, event

API = "/api/v1/carriers/andreani"


def make_order(user, **address_overrides):
    values = {
        "user": user,
        "recipient_name": "María Gómez",
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
    return Order.objects.create(user=user, address=address, external_number="1001", total_weight_kg=Decimal("2.5"))


class AccountTests(AndreaniTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")

    def test_cargar_la_cuenta_sin_devolver_la_contraseña(self):
        response = self.client.put(
            f"{API}/account/",
            {
                "environment": "qa",
                "username": USER,
                "password": PASSWORD,
                "client_code": "CL0001",
                "contracts": [{"code": "400006709", "label": "Domicilio", "kind": "home"}, {"code": "", "kind": "home"}],
                "sender_name": "Mi Tienda SRL",
                "origin_street": "Av. Siempre Viva",
                "origin_number": "742",
                "origin_postal_code": "1405",
                "origin_city": "CABA",
            },
            format="json",
            **auth_headers_for(self.user),
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertNotIn("password", response.data)
        self.assertTrue(response.data["has_password"])
        self.assertEqual(response.data["missing"], [])
        self.assertEqual(len(response.data["contracts"]), 1)
        account = CarrierAccount.objects.get(owner=self.user)
        self.assertEqual(account.password, PASSWORD)
        self.assertNotIn(PASSWORD, account.password_encrypted)

        # Probar la conexión inicia sesión en Andreani.
        test = self.client.post(f"{API}/account/test/", **auth_headers_for(self.user))
        self.assertTrue(test.data["ok"], test.data)
        self.assertEqual(self.andreani.logins, 1)

    def test_credenciales_rechazadas(self):
        self.client.put(f"{API}/account/", {"username": USER, "password": "otra"}, format="json", **auth_headers_for(self.user))
        test = self.client.post(f"{API}/account/test/", **auth_headers_for(self.user))
        self.assertEqual(test.status_code, 400)
        self.assertIn("rechazó", test.data["detail"])

    def test_cuenta_incompleta_dice_que_falta(self):
        response = self.client.get(f"{API}/account/", **auth_headers_for(self.user))
        self.assertFalse(response.data["exists"])
        self.assertIn("al menos un contrato", response.data["missing"])

    def test_contrato_de_tipo_invalido(self):
        response = self.client.put(
            f"{API}/account/", {"contracts": [{"code": "1", "kind": "aereo"}]}, format="json", **auth_headers_for(self.user)
        )
        self.assertEqual(response.status_code, 400)


class PayloadTests(AndreaniTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")
        self.account = self.make_account(self.user)

    def test_domicilio(self):
        payload = service.build_order_payload(self.account, make_order(self.user), "400006709", package_count=2)

        self.assertEqual(payload["contrato"], "400006709")
        self.assertEqual(payload["idPedido"], "1001")
        self.assertEqual(payload["origen"]["postal"], {"codigoPostal": "1405", "calle": "Av. Siempre Viva", "numero": "742", "localidad": "CABA", "pais": "Argentina"})
        destination = payload["destino"]["postal"]
        self.assertEqual((destination["codigoPostal"], destination["calle"], destination["numero"], destination["localidad"]), ("5000", "Av. Colón", "1234", "Córdoba"))
        self.assertEqual(destination["componentesDeDireccion"], [{"meta": "observaciones", "contenido": "Piso 3 B"}])
        self.assertEqual(payload["destinatario"], [{"nombreCompleto": "María Gómez"}])
        self.assertEqual(payload["remitente"]["nombreCompleto"], "Mi Tienda SRL")
        # El peso del pedido se reparte entre los bultos.
        self.assertEqual([package["kilos"] for package in payload["bultos"]], [1.25, 1.25])
        self.assertEqual(payload["bultos"][0]["referencias"], [{"meta": "idCliente", "contenido": "1001"}])

    def test_sucursal_o_punto_hop(self):
        payload = service.build_order_payload(self.account, make_order(self.user), "400006710", branch_id="4327")
        self.assertEqual(payload["destino"], {"sucursal": {"id": "4327"}})


class ShipmentTests(AndreaniTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")
        self.account = self.make_account(self.user)
        self.order = make_order(self.user)

    def _create(self, **data):
        payload = {"order_ids": [self.order.pk], "contract": "400006709"}
        payload.update(data)
        return self.client.post(f"{API}/shipments/", payload, format="json", **auth_headers_for(self.user))

    def test_crear_envio_deja_el_numero_como_seguimiento_y_en_el_rotulo(self):
        response = self._create()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["created"], 1, response.data)
        shipment = CarrierShipment.objects.get(order=self.order)
        self.order.refresh_from_db()
        self.assertEqual(self.order.carrier, "Andreani")
        self.assertEqual(self.order.tracking_number, shipment.tracking_number)
        self.assertEqual(self.order.tracking_url, f"https://www.andreani.com/envio/{shipment.tracking_number}")
        # Creado no es despachado: eso lo dice Andreani cuando lo admite.
        self.assertEqual(self.order.status, Order.Status.PREPARING)
        # El código de barras del rótulo (y el número debajo) es el de Andreani.
        self.assertEqual(build_label_context(order=self.order)["tracking"], shipment.tracking_number)
        # Un solo login para todo.
        self.assertEqual(self.andreani.logins, 1)

    def test_no_se_crea_dos_veces(self):
        self._create()
        again = self._create()
        self.assertEqual(again.data["created"], 0)
        self.assertIn("ya tiene el envío", again.data["results"][0]["detail"])

    def test_contrato_de_sucursal_pide_la_sucursal(self):
        response = self._create(contract="400006710")
        self.assertIn("elegir la sucursal", response.data["results"][0]["detail"])

        ok = self._create(contract="400006710", branches={str(self.order.pk): {"id": "4327", "name": "PUNTO ANDREANI HOP"}})
        self.assertEqual(ok.data["created"], 1, ok.data)
        shipment = CarrierShipment.objects.get(order=self.order)
        self.assertEqual((shipment.delivery_kind, shipment.branch_id), ("branch", "4327"))
        self.assertEqual(self.andreani.orders[-1]["destino"], {"sucursal": {"id": "4327"}})

    def test_un_pedido_que_falla_no_frena_a_los_demas(self):
        broken = make_order(self.user, street="", postal_code="")
        foreign = make_order(make_user("otro@example.com"))
        response = self._create(order_ids=[broken.pk, self.order.pk, foreign.pk])

        self.assertEqual((response.data["created"], response.data["failed"]), (1, 2))
        details = {item["order_id"]: item["detail"] for item in response.data["results"]}
        self.assertIn("calle, localidad y código postal", details[broken.pk])
        self.assertIn("no es tuyo", details[foreign.pk])

    def test_rechazo_de_andreani_llega_con_su_mensaje(self):
        self.account.contracts = [{"code": "000", "label": "", "kind": "home"}]
        self.account.save()
        response = self._create(contract="000")
        self.assertIn("El contrato es incorrecto.", response.data["results"][0]["detail"])
        self.assertFalse(CarrierShipment.objects.exists())

    def test_cuenta_incompleta_no_crea(self):
        self.account.origin_street = ""
        self.account.save()
        response = self._create()
        self.assertIn("domicilio de origen", response.data["results"][0]["detail"])

    def test_etiquetas_pdf_y_zpl(self):
        self._create()
        shipment = CarrierShipment.objects.get(order=self.order)

        pdf = self.client.get(f"{API}/shipments/{shipment.pk}/label/", **auth_headers_for(self.user))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        zpl = self.client.get(f"{API}/shipments/{shipment.pk}/label/", {"file_type": "zpl"}, **auth_headers_for(self.user))
        self.assertTrue(zpl.content.startswith(b"^XA"))

        other = make_order(self.user)
        self.client.post(f"{API}/shipments/", {"order_ids": [other.pk], "contract": "400006709"}, format="json", **auth_headers_for(self.user))
        ids = list(CarrierShipment.objects.values_list("pk", flat=True))
        bundle = self.client.post(f"{API}/shipments/labels/", {"shipment_ids": ids}, format="json", **auth_headers_for(self.user))
        self.assertEqual(len(zipfile.ZipFile(io.BytesIO(bundle.content)).namelist()), 2)
        zpl_all = self.client.post(f"{API}/shipments/labels/", {"shipment_ids": ids, "file_type": "zpl"}, format="json", **auth_headers_for(self.user))
        self.assertEqual(zpl_all.content.count(b"^XA"), 2)

    def test_etiqueta_de_un_envio_ajeno(self):
        self._create()
        shipment = CarrierShipment.objects.get(order=self.order)
        response = self.client.get(f"{API}/shipments/{shipment.pk}/label/", **auth_headers_for(make_user("otro@example.com")))
        self.assertEqual(response.status_code, 404)

    def test_cancelar_le_saca_el_seguimiento_al_pedido(self):
        self._create()
        shipment = CarrierShipment.objects.get(order=self.order)

        response = self.client.post(f"{API}/shipments/{shipment.pk}/cancel/", **auth_headers_for(self.user))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.andreani.cancelled[0]["datos"], {"contrato": "400006709", "numeroAndreani": [shipment.tracking_number]})
        self.order.refresh_from_db()
        self.assertEqual((self.order.carrier, self.order.tracking_number), ("", ""))
        # Y se puede volver a crear.
        self.assertEqual(self._create().data["created"], 1)

    def test_token_vencido_se_renueva_solo(self):
        self._create()
        self.andreani.valid_token = "otro-token"  # Andreani lo invalidó antes de tiempo.
        other = make_order(self.user)
        response = self.client.post(f"{API}/shipments/", {"order_ids": [other.pk], "contract": "400006709"}, format="json", **auth_headers_for(self.user))
        self.assertEqual(response.data["created"], 1, response.data)
        self.assertEqual(self.andreani.logins, 2)


class QuoteTests(AndreaniTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")
        self.account = self.make_account(self.user)
        self.order = make_order(self.user)

    def _quote(self, **data):
        payload = {"order_ids": [self.order.pk], "contract": "400006709"}
        payload.update(data)
        return self.client.post(f"{API}/shipments/quote/", payload, format="json", **auth_headers_for(self.user))

    def test_cotiza_con_el_contrato_el_cliente_y_el_peso_del_pedido(self):
        response = self._quote()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["quoted"], 1, response.data)
        result = response.data["results"][0]
        # 2,5 kg x 1000 x 1,21
        self.assertEqual(result["price"], "3025.00")
        self.assertEqual(result["price_without_tax"], "2500.00")
        self.assertEqual(response.data["total"], "3025.00")
        params = self.andreani.calls_to("GET", "/v1/tarifas")[0]["params"]
        self.assertEqual(
            (params["cpDestino"], params["contrato"], params["cliente"], params["bultos[0][kilos]"], params["bultos[0][volumen]"]),
            ("5000", "400006709", "CL0001", "2.5", "4000"),
        )
        # Cotizar no crea nada.
        self.assertFalse(CarrierShipment.objects.exists())
        self.assertFalse(self.andreani.orders)

    def test_el_peso_se_reparte_entre_los_bultos(self):
        self._quote(package_count=2)
        params = self.andreani.calls_to("GET", "/v1/tarifas")[0]["params"]
        self.assertEqual((params["bultos[0][kilos]"], params["bultos[1][kilos]"]), ("1.25", "1.25"))

    def test_sin_codigo_de_cliente_no_cotiza_y_un_pedido_ajeno_tampoco(self):
        foreign = make_order(make_user("otro@example.com"))
        self.account.client_code = ""
        self.account.save()
        response = self._quote(order_ids=[self.order.pk, foreign.pk])

        self.assertEqual((response.data["quoted"], response.data["failed"]), (0, 2))
        details = {item["order_id"]: item["detail"] for item in response.data["results"]}
        self.assertIn("código de cliente", details[self.order.pk])
        self.assertIn("no es tuyo", details[foreign.pk])
        self.assertFalse(self.andreani.calls_to("GET", "/v1/tarifas"))

    def test_al_crear_queda_lo_cotizado_y_sin_cotizacion_igual_se_crea(self):
        created = self.client.post(
            f"{API}/shipments/", {"order_ids": [self.order.pk], "contract": "400006709"}, format="json", **auth_headers_for(self.user)
        )
        self.assertEqual(created.data["results"][0]["quoted_price"], "3025.00", created.data)

        other = make_order(self.user)
        self.account.client_code = ""
        self.account.save()
        again = self.client.post(
            f"{API}/shipments/", {"order_ids": [other.pk], "contract": "400006709"}, format="json", **auth_headers_for(self.user)
        )
        self.assertEqual(again.data["created"], 1, again.data)
        self.assertIsNone(CarrierShipment.objects.get(order=other).quoted_price)


class BranchTests(AndreaniTestMixin, APITestCase):
    def test_sucursales_y_puntos_hop_por_codigo_postal(self):
        user = make_user("comercio@example.com")
        self.make_account(user)
        response = self.client.get(f"{API}/branches/", {"cp": "C1414ABC"}, **auth_headers_for(user))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.andreani.calls_to("GET", "/v2/sucursales")[0]["params"], {"codigoPostal": "1414"})
        hop = next(branch for branch in response.data["results"] if branch["id"] == "4327")
        self.assertTrue(hop["is_hop"])
        self.assertFalse(next(branch for branch in response.data["results"] if branch["id"] == "96")["is_hop"])


@override_settings(ANDREANI_TRACKING_POLL_MINUTES=30)
class TrackingTests(AndreaniTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("comercio@example.com")
        self.account = self.make_account(self.user)
        self.order = make_order(self.user)
        self.shipment = service.create_shipment(None, self.account, self.order, "400006709")
        self.number = self.shipment.tracking_number

    def _sync(self):
        changed = service.sync_shipment(CarrierShipment.objects.get(pk=self.shipment.pk))
        self.shipment.refresh_from_db()
        self.order.refresh_from_db()
        return changed

    def test_admitido_en_camino_y_entregado(self):
        self.andreani.traces[self.number] = [event("Admision", "2026-10-08T10:00:00.000")]
        self.assertTrue(self._sync())
        self.assertEqual(self.shipment.status, CarrierShipment.Status.IN_TRANSIT)
        # Admitido por Andreani = despachado (recién ahí se le avisa a la tienda).
        self.assertEqual(self.order.status, Order.Status.DISPATCHED)

        self.andreani.traces[self.number].append(event("EnvioDespachado", "2026-10-08T20:00:00.000"))
        self._sync()
        self.assertEqual(self.order.status, Order.Status.IN_TRANSIT)

        self.andreani.traces[self.number] += [
            event("Impresion", "2026-10-08T11:00:00.000"),
            event("EnvioEntregado", "2026-10-09T15:30:00.000", Motivo="Entregado", Estado="Entregado", Sucursal="CORDOBA"),
        ]
        self._sync()
        self.assertEqual(self.shipment.status, CarrierShipment.Status.DELIVERED)
        self.assertEqual(self.shipment.carrier_status, "Entregado")
        self.assertEqual(self.order.status, Order.Status.DELIVERED)
        self.assertEqual(CarrierShipmentEvent.objects.filter(shipment=self.shipment).count(), 4)
        # Releer las mismas trazas no duplica movimientos.
        self._sync()
        self.assertEqual(CarrierShipmentEvent.objects.filter(shipment=self.shipment).count(), 4)

    def test_entregado_en_la_devolucion_no_es_entregado_al_comprador(self):
        self.andreani.traces[self.number] = [
            event("Admision", "2026-10-08T10:00:00.000"),
            event("EnvioNoEntregado", "2026-10-09T10:00:00.000", Motivo="Rehusado"),
            event("EnvioEntregado", "2026-10-12T10:00:00.000", cycle="Drop"),
        ]
        self._sync()
        self.assertEqual(self.shipment.status, CarrierShipment.Status.RETURNING)
        self.assertNotEqual(self.order.status, Order.Status.DELIVERED)

    def test_esperando_retiro_en_punto_hop(self):
        self.andreani.traces[self.number] = [event("ComienzoCustodiaEnSucursal", "2026-10-09T10:00:00.000", Sucursal="PUNTO ANDREANI HOP")]
        self._sync()
        self.assertEqual(self.shipment.status, CarrierShipment.Status.AT_BRANCH)
        self.assertEqual(self.order.status, Order.Status.IN_TRANSIT)

    def test_el_worker_consulta_solo_lo_que_toca_y_desactiva_credenciales_rechazadas(self):
        self.andreani.traces[self.number] = [event("Admision", "2026-10-08T10:00:00.000")]
        self.assertEqual(sync_due_shipments(), 1)
        # Recién consultado: no se vuelve a consultar hasta dentro de 30 min.
        calls = len(self.andreani.calls_to("GET", f"/v3/envios/{self.number}/trazas"))
        sync_due_shipments()
        self.assertEqual(len(self.andreani.calls_to("GET", f"/v3/envios/{self.number}/trazas")), calls)

        CarrierShipment.objects.update(last_checked_at=timezone.now() - timedelta(hours=1))
        self.andreani.valid_token = "revocado"
        CarrierAccount.objects.filter(pk=self.account.pk).update(password_encrypted=CarrierAccount(password="mala").password_encrypted)
        sync_due_shipments()
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_active)
        self.assertIn("rechazó", self.account.last_error)
