"""Opciones del carrier en la tienda (``carrier_options``): Tiendanube descarta
toda tarifa cuyo ``code`` no tenga una opción activa, así que hay una por cada
código que el callback puede contestar."""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import override_settings

from apps.carriers.andreani import checkout as andreani_checkout
from apps.carriers.andreani.tests.fake_andreani import AndreaniTestMixin
from apps.integrations.events import process_due_events
from apps.integrations.models import IntegrationEvent, ShippingRate
from apps.integrations.providers.tiendanube import carrier_options
from apps.integrations.providers.tiendanube import labels as store_labels
from apps.integrations.providers.tiendanube import rates
from apps.integrations.tests.helpers import auth_headers_for

from .test_tiendanube_labels import LABEL_SETTINGS, StoreLabelTestCase

CARRIER_SETTINGS = dict(LABEL_SETTINGS, STORE_LABEL_CARRIER_NAME="Rótulos")
CARRIER_ID = 4242
OPTIONS_PATH = f"shipping_carriers/{CARRIER_ID}/options"


@override_settings(**CARRIER_SETTINGS)
class CarrierOptionsTestCase(StoreLabelTestCase):
    def setUp(self):
        super().setUp()
        self.api.set("GET", "shipping_carriers", 200, [])
        self.api.set("POST", "shipping_carriers", 201, {"id": CARRIER_ID, "name": "Rótulos"})
        self.api.set("GET", OPTIONS_PATH, 200, [])
        self.api.set("POST", OPTIONS_PATH, 201, {"id": 1})

    def rate(self, code="standard", name="Envío estándar", **overrides):
        values = {
            "connection": self.connection,
            "option_code": code,
            "option_name": name,
            "postal_code_from": "1000",
            "postal_code_to": "1999",
            "price": Decimal("4500"),
        }
        values.update(overrides)
        return ShippingRate.objects.create(**values)

    def enable_andreani(self, name="Andreani a domicilio"):
        andreani_checkout.save_config(
            self.connection, {"enabled": True, "contract": "400006709", "name": name}
        )

    def created(self):
        return [call["json"] for call in self.api.calls_to("POST", OPTIONS_PATH)]

    def sync_events(self):
        return IntegrationEvent.objects.filter(event_type=carrier_options.SYNC_EVENT)

    def mark_registered(self):
        self.connection.preferences = {
            **(self.connection.preferences or {}),
            store_labels.CARRIER_ID_PREFERENCE: str(CARRIER_ID),
        }
        self.connection.save(update_fields=["preferences"])


class RegisterCarrierOptionsTests(CarrierOptionsTestCase):
    def test_el_alta_crea_una_opcion_por_codigo_de_la_tabla_y_andreani(self):
        self.rate("standard", "Envío estándar")
        self.rate("standard", "Envío estándar", postal_code_from="2000", postal_code_to="2999")
        self.rate("express", "Envío express")
        self.rate("viejo", "Ya no se usa", is_active=False)
        self.enable_andreani("Andreani")

        store_labels.register_carrier(self.connection)

        self.assertEqual(
            self.created(),
            [
                {"code": "andreani", "name": "Andreani"},
                {"code": "express", "name": "Envío express"},
                {"code": "standard", "name": "Envío estándar"},
            ],
        )
        self.connection.refresh_from_db()
        state = carrier_options.state(self.connection)
        self.assertEqual(state["status"], "synced")
        self.assertEqual(state["codes"], ["andreani", "express", "standard"])

    def test_sin_andreani_activado_no_hay_opcion_andreani(self):
        self.rate()
        andreani_checkout.save_config(self.connection, {"enabled": False, "contract": "400006709"})

        store_labels.register_carrier(self.connection)

        self.assertEqual([body["code"] for body in self.created()], ["standard"])

    def test_solo_andreani_con_el_nombre_por_defecto(self):
        andreani_checkout.save_config(self.connection, {"enabled": True, "contract": "400006709", "name": ""})

        store_labels.register_carrier(self.connection)

        self.assertEqual(self.created(), [{"code": "andreani", "name": andreani_checkout.DEFAULT_NAME}])

    def test_si_la_tabla_usa_el_codigo_andreani_gana_la_tabla(self):
        self.rate("andreani", "Andreani de mi tabla")
        self.enable_andreani("Andreani cotizado")

        store_labels.register_carrier(self.connection)

        self.assertEqual(self.created(), [{"code": "andreani", "name": "Andreani de mi tabla"}])

    def test_registrar_de_nuevo_no_duplica_opciones_y_solo_renombra(self):
        self.rate("standard", "Envío estándar")
        self.rate("express", "Express nuevo nombre")
        self.api.set(
            "GET",
            OPTIONS_PATH,
            200,
            [
                {"id": 10, "code": "standard", "name": "Envío estándar", "active": True, "additional_cost": 500.0},
                {"id": 11, "code": "express", "name": "Express", "active": True},
                {"id": 12, "code": "otro", "name": "De otra época", "active": True},
            ],
        )
        self.api.set("PUT", f"{OPTIONS_PATH}/11", 200, {"id": 11})

        store_labels.register_carrier(self.connection)

        self.assertEqual(self.created(), [])
        self.assertEqual(self.api.calls_to("PUT", f"{OPTIONS_PATH}/10"), [])
        # Solo el nombre: costo y días adicionales son del comerciante.
        self.assertEqual(
            [call["json"] for call in self.api.calls_to("PUT", f"{OPTIONS_PATH}/11")], [{"name": "Express nuevo nombre"}]
        )
        # Una opción de un código que ya no cotizamos no se borra.
        self.assertFalse([call for call in self.api.calls if call["method"] == "DELETE"])

    def test_una_opcion_que_el_comerciante_apago_no_se_reactiva(self):
        self.rate()
        self.api.set("GET", OPTIONS_PATH, 200, [{"id": 10, "code": "standard", "name": "Envío estándar", "active": False}])

        store_labels.register_carrier(self.connection)

        self.assertEqual(self.created(), [])
        self.assertFalse([call for call in self.api.calls if call["method"] == "PUT" and call["path"].startswith(OPTIONS_PATH)])
        self.connection.refresh_from_db()
        self.assertEqual(carrier_options.state(self.connection)["inactive"], ["standard"])

    def test_si_fallan_las_opciones_el_carrier_queda_y_se_reintenta_en_el_worker(self):
        self.rate()
        self.api.set("GET", OPTIONS_PATH, 500, {})

        carrier = store_labels.register_carrier(self.connection)

        self.assertEqual(carrier["id"], CARRIER_ID)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.preferences[store_labels.CARRIER_ID_PREFERENCE], str(CARRIER_ID))
        state = carrier_options.state(self.connection)
        self.assertEqual(state["status"], "failed")
        self.assertIn("500", state["error"])
        self.assertEqual(self.sync_events().count(), 1)

        self.api.set("GET", OPTIONS_PATH, 200, [])
        process_due_events()

        self.assertEqual(self.sync_events().get().status, IntegrationEvent.Status.DONE)
        self.assertEqual([body["code"] for body in self.created()], ["standard"])
        self.connection.refresh_from_db()
        self.assertEqual(carrier_options.state(self.connection)["status"], "synced")

    def test_el_comando_informa_las_opciones(self):
        self.rate()
        out = StringIO()

        call_command("register_store_carrier", str(self.connection.pk), stdout=out)

        self.assertIn("Opciones del carrier: standard", out.getvalue())


class ResyncTests(CarrierOptionsTestCase):
    URL = "/api/v1/integrations/shipping-rates/"

    def payload(self, **overrides):
        data = {
            "connection": self.connection.pk,
            "option_code": "express",
            "option_name": "Envío express",
            "postal_code_from": "1000",
            "postal_code_to": "1999",
            "price": "4500",
        }
        data.update(overrides)
        return data

    def test_cargar_una_tarifa_con_codigo_nuevo_sincroniza_en_el_worker(self):
        self.mark_registered()

        response = self.client.post(self.URL, self.payload(), format="json", **auth_headers_for(self.owner))
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.sync_events().count(), 1)
        # Ninguna llamada a la API dentro del pedido del comerciante.
        self.assertEqual(self.api.calls_to("GET", OPTIONS_PATH), [])

        process_due_events()

        self.assertEqual(self.created(), [{"code": "express", "name": "Envío express"}])

    def test_varios_cambios_seguidos_encolan_un_solo_evento(self):
        self.mark_registered()

        for code in ("express", "rapido", "lento"):
            self.client.post(self.URL, self.payload(option_code=code), format="json", **auth_headers_for(self.owner))

        self.assertEqual(self.sync_events().count(), 1)
        process_due_events()
        self.assertEqual([body["code"] for body in self.created()], ["express", "lento", "rapido"])

    def test_sin_carrier_dado_de_alta_no_se_encola_nada(self):
        self.client.post(self.URL, self.payload(), format="json", **auth_headers_for(self.owner))

        self.assertEqual(self.sync_events().count(), 0)

    def test_si_borraron_el_carrier_el_evento_falla_sin_reintentos(self):
        self.mark_registered()
        self.rate()
        del self.api.routes[("GET", OPTIONS_PATH)]  # 404

        carrier_options.enqueue_sync(self.connection)
        process_due_events()

        event = self.sync_events().get()
        self.assertEqual(event.status, IntegrationEvent.Status.FAILED)
        self.connection.refresh_from_db()
        self.assertEqual(carrier_options.state(self.connection)["status"], "failed")


@override_settings(**CARRIER_SETTINGS)
class AndreaniSettingResyncTests(CarrierOptionsTestCase):
    URL = "/api/v1/carriers/andreani/checkout/"

    def setUp(self):
        super().setUp()
        # Solo la cuenta: guardar la configuración no llama a Andreani, y su
        # API falsa reemplazaría a la de Tiendanube (las dos parchean requests).
        AndreaniTestMixin.make_account(self, self.owner)
        self.mark_registered()

    def put(self, **values):
        data = {"store": self.connection.pk, "enabled": True, "contract": "400006709", "name": "Andreani"}
        data.update(values)
        return self.client.put(self.URL, data, format="json", **auth_headers_for(self.owner))

    def test_activar_andreani_crea_su_opcion(self):
        response = self.put()
        self.assertEqual(response.status_code, 200, response.data)

        process_due_events()

        self.assertEqual(self.created(), [{"code": "andreani", "name": "Andreani"}])

    def test_cambiar_solo_el_recargo_no_toca_las_opciones(self):
        self.put()
        process_due_events()
        events_before = self.sync_events().count()

        self.put(surcharge_percent="10")

        self.assertEqual(self.sync_events().count(), events_before)


@override_settings(**CARRIER_SETTINGS)
class RatesCallbackDiagnosticTests(CarrierOptionsTestCase):
    def cart(self, options):
        return {
            "destination": {"postal_code": "1602"},
            "items": [{"grams": 500, "quantity": 1}],
            "carrier": {"id": str(CARRIER_ID), "options": options},
        }

    def test_avisa_en_el_log_un_codigo_sin_opcion(self):
        self.rate("standard")
        self.rate("express")

        with self.assertLogs("apps.integrations.providers.tiendanube.rates", level="WARNING") as logs:
            body = rates.quote(self.connection, self.cart([{"code": "standard", "name": "Estándar"}]))

        # La respuesta no cambia: se publican todas, filtra la plataforma.
        self.assertEqual({rate["code"] for rate in body["rates"]}, {"standard", "express"})
        self.assertIn("express", logs.output[0])

    def test_con_todas_las_opciones_no_avisa_nada(self):
        self.rate("standard")

        with self.assertNoLogs("apps.integrations.providers.tiendanube.rates", level="WARNING"):
            rates.quote(self.connection, self.cart([{"code": "standard"}]))
