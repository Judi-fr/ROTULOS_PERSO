"""Tests de la cotización del checkout de VTEX (``freight.py``): la tabla de
tarifas traducida a filas de flete y su publicación como políticas de envío."""

from decimal import Decimal

from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from apps.integrations.events import process_due_events
from apps.integrations.models import IntegrationEvent, ShippingRate, StoreConnection
from apps.integrations.providers.vtex.freight import freight_rows
from apps.integrations.stores import PUSH_SHIPPING_RATES_EVENT
from apps.integrations.tests.helpers import auth_headers_for, make_user

from .fake_vtex import VTEX_SETTINGS, VtexTestMixin


def rate(pk, postal_from, postal_to, up_to_kg, price, code="standard", **extra):
    """Una ``ShippingRate`` sin guardar, para probar la traducción a filas."""
    return ShippingRate(
        pk=pk,
        option_code=code,
        option_name="Envío estándar",
        postal_code_from=postal_from,
        postal_code_to=postal_to,
        weight_up_to_kg=None if up_to_kg is None else Decimal(str(up_to_kg)),
        price=Decimal(str(price)),
        **extra,
    )


def brackets(rows):
    return [(r["zipCodeStart"], r["zipCodeEnd"], r["weightStart"], r["weightEnd"], r["absoluteMoneyCost"]) for r in rows]


class FreightRowsTests(TestCase):
    def test_franjas_de_peso_consecutivas_y_la_sin_tope_arriba(self):
        rows = freight_rows([rate(1, "1000", "1999", 5, 2000), rate(2, "1000", "1999", 1, 1000), rate(3, "1000", "1999", None, 9000)])
        self.assertEqual(
            brackets(rows),
            [
                ("1000", "1999", 0, 1000, "1000.00"),
                ("1000", "1999", 1001, 5000, "2000.00"),
                ("1000", "1999", 5001, 100_000_000, "9000.00"),
            ],
        )
        self.assertEqual({(r["country"], r["timeCost"]) for r in rows}, {("ARG", "5.00:00:00")})

    def test_rangos_que_se_pisan_dan_lo_mismo_que_la_cotizacion(self):
        # Un CP suelto con su propia franja dentro de una zona: en ese CP
        # convive con las franjas de la zona, como en matching_rates.
        rows = freight_rows([rate(1, "1000", "1999", 5, 2000), rate(2, "1500", "1500", 10, 3500)])
        self.assertEqual(
            brackets(rows),
            [
                ("1000", "1499", 0, 5000, "2000.00"),
                ("1500", "1500", 0, 5000, "2000.00"),
                ("1500", "1500", 5001, 10000, "3500.00"),
                ("1501", "1999", 0, 5000, "2000.00"),
            ],
        )

    def test_zonas_vecinas_con_las_mismas_tarifas_se_juntan_y_los_huecos_quedan(self):
        rows = freight_rows([rate(1, "1000", "1499", None, 100), rate(2, "1500", "1999", None, 100), rate(3, "3000", "3000", None, 100)])
        self.assertEqual([(r["zipCodeStart"], r["zipCodeEnd"]) for r in rows], [("1000", "1999"), ("3000", "3000")])

    def test_plazo_de_entrega(self):
        rows = freight_rows([rate(1, "1000", "1000", None, 100, delivery_days_min=2, delivery_days_max=4)])
        self.assertEqual(rows[0]["timeCost"], "4.00:00:00")


@override_settings(**VTEX_SETTINGS)
class PublishRatesTests(VtexTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("comercio@example.com")
        self.connection = self._connection(owner=self.owner)
        self.cheap = ShippingRate.objects.create(
            connection=self.connection, postal_code_from="1000", postal_code_to="1999", weight_up_to_kg=Decimal("5"), price=Decimal("2000")
        )

    def _publish(self, enabled=True, store=None):
        return self.client.post(
            f"/api/v1/integrations/stores/{store or self.connection.pk}/publish-rates/",
            {"enabled": enabled},
            format="json",
            **auth_headers_for(self.owner),
        )

    def _state(self):
        self.connection.refresh_from_db()
        return self.connection.preferences["rates_push"]

    def test_publicar_crea_la_politica_sube_la_tabla_y_avisa_el_muelle(self):
        response = self._publish()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["rates_push"]["status"], "pending")
        process_due_events()

        policy = self.vtex.policies["rotulos-standard"]
        self.assertEqual((policy["name"], policy["shippingMethod"], policy["isActive"]), ("Envío estándar", "Envío estándar", True))
        self.assertEqual(brackets(self.vtex.freights["rotulos-standard"]), [("1000", "1999", 0, 5000, "2000.00")])
        state = self._state()
        self.assertEqual((state["status"], state["rows"], state["unlinked_policies"]), ("published", 1, ["rotulos-standard"]))

        # Asociada al muelle en VTEX: el próximo repaso ya no la marca.
        self.vtex.docks[0]["freightTableIds"] = ["rotulos-standard"]
        self._publish()
        process_due_events()
        self.assertEqual(self._state()["unlinked_policies"], [])

    def test_cambiar_la_tabla_publicada_reemplaza_las_filas_en_vtex(self):
        self._publish()
        process_due_events()

        response = self.client.patch(
            f"/api/v1/integrations/shipping-rates/{self.cheap.pk}/", {"price": "2500"}, format="json", **auth_headers_for(self.owner)
        )
        self.assertEqual(response.status_code, 200, response.data)
        process_due_events()

        self.assertEqual(brackets(self.vtex.freights["rotulos-standard"]), [("1000", "1999", 0, 5000, "2500.00")])

    def test_despublicar_borra_lo_subido(self):
        self._publish()
        process_due_events()

        self._publish(enabled=False)
        process_due_events()

        self.assertEqual(self.vtex.freights["rotulos-standard"], [])
        self.assertEqual(self._state()["rows"], 0)
        self.assertEqual(self.connection.preferences["vtex_freight"], {})

    def test_sin_publicar_los_cambios_no_tocan_vtex(self):
        self.client.patch(
            f"/api/v1/integrations/shipping-rates/{self.cheap.pk}/", {"price": "2500"}, format="json", **auth_headers_for(self.owner)
        )
        self.assertFalse(IntegrationEvent.objects.filter(event_type=PUSH_SHIPPING_RATES_EVENT).exists())

    def test_sin_tarifas_no_se_publica(self):
        self.cheap.delete()
        response = self._publish()
        self.assertEqual(response.status_code, 400)

    def test_plataforma_que_cotiza_en_el_momento_no_publica(self):
        other = StoreConnection.objects.create(platform="woocommerce", external_store_id="mitienda.com.ar", owner=self.owner)
        self.assertEqual(self._publish(store=other.pk).status_code, 400)

    def test_sin_permiso_de_logistica_queda_el_error_a_la_vista(self):
        self.vtex.denied.add("/api/logistics/pvt/shipping-policies/rotulos-standard")
        self._publish()
        process_due_events()

        state = self._state()
        self.assertEqual(state["status"], "failed")
        self.assertIn("Logistics shipping full access", state["error"])
        # La tienda sigue conectada: lo que falla es solo la publicación.
        self.assertEqual(self.connection.status, StoreConnection.Status.ACTIVE)
        self.assertEqual(IntegrationEvent.objects.get(event_type=PUSH_SHIPPING_RATES_EVENT).status, IntegrationEvent.Status.FAILED)
