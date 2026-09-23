"""Cotización de envíos en el checkout de la tienda (``shipping_rates``)."""

from decimal import Decimal

from django.test import override_settings

from apps.integrations import shipping_rates, store_labels
from apps.integrations.models import ShippingRate, StoreConnection

from .test_tiendanube_sync import TiendanubeTestCase
from .test_tiendanube_oauth import TEST_SETTINGS, auth_headers_for, make_user

RATE_SETTINGS = dict(TEST_SETTINGS, INTEGRATIONS_PUBLIC_BASE_URL="https://rotulos.example.com")


def cart(postal_code="1602", items=None):
    return {
        "cart_id": "123",
        "store_id": 1,
        "currency": "ARS",
        "destination": {"postal_code": postal_code, "city": "Vicente López"},
        "items": items if items is not None else [{"grams": 500, "quantity": 2}],
    }


@override_settings(**RATE_SETTINGS)
class ShippingRateLookupTests(TiendanubeTestCase):
    """La tabla: qué fila se elige para un destino y un peso."""

    def rate(self, **overrides):
        data = {
            "connection": self.connection,
            "option_code": "standard",
            "option_name": "Envío estándar",
            "postal_code_from": "1000",
            "postal_code_to": "1999",
            "weight_up_to_kg": Decimal("5"),
            "price": Decimal("4500"),
        }
        data.update(overrides)
        return ShippingRate.objects.create(**data)

    def test_el_cp_del_comprador_encuentra_su_zona(self):
        self.rate()

        body = shipping_rates.quote(self.connection, cart(postal_code="1602"))

        self.assertEqual(len(body["rates"]), 1)
        self.assertEqual(body["rates"][0]["price"], 4500.0)
        self.assertEqual(body["rates"][0]["code"], "standard")
        self.assertEqual(body["rates"][0]["type"], "ship")

    def test_un_cp_fuera_de_toda_zona_no_se_cotiza(self):
        # Preferimos no ofrecer el envío antes que inventarle un precio.
        self.rate()

        body = shipping_rates.quote(self.connection, cart(postal_code="5000"))

        self.assertEqual(body["rates"], [])

    def test_un_cpa_se_compara_por_sus_digitos(self):
        self.rate()

        body = shipping_rates.quote(self.connection, cart(postal_code="C1602ABC"))

        self.assertEqual(len(body["rates"]), 1)

    def test_gana_la_franja_de_peso_mas_ajustada(self):
        self.rate(weight_up_to_kg=Decimal("1"), price=Decimal("2000"))
        self.rate(weight_up_to_kg=Decimal("5"), price=Decimal("4500"))
        self.rate(weight_up_to_kg=Decimal("25"), price=Decimal("9000"))

        # 3 kg: no entra en la de 1, sí en la de 5.
        body = shipping_rates.quote(
            self.connection, cart(items=[{"grams": 3000, "quantity": 1}])
        )

        self.assertEqual(body["rates"][0]["price"], 4500.0)

    def test_un_paquete_mas_pesado_que_toda_franja_no_se_cotiza(self):
        self.rate(weight_up_to_kg=Decimal("5"))

        body = shipping_rates.quote(
            self.connection, cart(items=[{"grams": 50000, "quantity": 1}])
        )

        self.assertEqual(body["rates"], [])

    def test_una_franja_sin_tope_cubre_cualquier_peso(self):
        self.rate(weight_up_to_kg=None, price=Decimal("12000"))

        body = shipping_rates.quote(
            self.connection, cart(items=[{"grams": 80000, "quantity": 1}])
        )

        self.assertEqual(body["rates"][0]["price"], 12000.0)

    def test_la_franja_con_tope_le_gana_a_la_sin_tope(self):
        self.rate(weight_up_to_kg=None, price=Decimal("12000"))
        self.rate(weight_up_to_kg=Decimal("5"), price=Decimal("4500"))

        body = shipping_rates.quote(self.connection, cart())

        self.assertEqual(len(body["rates"]), 1)
        self.assertEqual(body["rates"][0]["price"], 4500.0)

    def test_se_devuelve_una_tarifa_por_modalidad(self):
        self.rate(option_code="standard", option_name="Estándar", price=Decimal("4500"))
        self.rate(option_code="express", option_name="Express", price=Decimal("8000"))

        body = shipping_rates.quote(self.connection, cart())

        codes = sorted(rate["code"] for rate in body["rates"])
        self.assertEqual(codes, ["express", "standard"])

    def test_una_tarifa_inactiva_no_se_ofrece(self):
        self.rate(is_active=False)

        self.assertEqual(shipping_rates.quote(self.connection, cart())["rates"], [])

    def test_la_tabla_de_otra_tienda_no_se_usa(self):
        otra = StoreConnection.objects.create(
            platform="tiendanube", external_store_id="999", owner=self.owner
        )
        self.rate(connection=otra)

        self.assertEqual(shipping_rates.quote(self.connection, cart())["rates"], [])

    def test_el_peso_sale_de_los_gramos_por_cantidad(self):
        self.rate(weight_up_to_kg=Decimal("1"), price=Decimal("2000"))
        self.rate(weight_up_to_kg=Decimal("5"), price=Decimal("4500"))

        # 3 x 400 g = 1,2 kg: se pasa de la franja de 1 kg.
        body = shipping_rates.quote(
            self.connection, cart(items=[{"grams": 400, "quantity": 3}])
        )

        self.assertEqual(body["rates"][0]["price"], 4500.0)

    def test_los_plazos_solo_se_prometen_si_estan_cargados(self):
        self.rate(delivery_days_min=None, delivery_days_max=None)

        rate = shipping_rates.quote(self.connection, cart())["rates"][0]

        self.assertNotIn("min_delivery_date", rate)
        self.assertNotIn("max_delivery_date", rate)

    def test_con_plazos_cargados_se_mandan_como_fechas(self):
        self.rate(delivery_days_min=2, delivery_days_max=5)

        rate = shipping_rates.quote(self.connection, cart())["rates"][0]

        self.assertIn("min_delivery_date", rate)
        self.assertIn("max_delivery_date", rate)


@override_settings(**RATE_SETTINGS)
class RatesCallbackTests(TiendanubeTestCase):
    """El endpoint público que pega la plataforma en cada checkout."""

    def setUp(self):
        super().setUp()
        ShippingRate.objects.create(
            connection=self.connection,
            postal_code_from="1000",
            postal_code_to="1999",
            weight_up_to_kg=Decimal("5"),
            price=Decimal("4500"),
        )
        self.url = (
            f"/api/v1/integrations/tiendanube/rates/"
            f"{store_labels.make_callback_token(self.connection)}"
        )

    def test_contesta_las_tarifas_sin_sesion(self):
        response = self.client.post(self.url, data=cart(), format="json")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["rates"][0]["price"], 4500.0)

    def test_un_token_alterado_da_404(self):
        response = self.client.post(f"{self.url}x", data=cart(), format="json")

        self.assertEqual(response.status_code, 404)

    def test_una_tienda_que_desinstalo_no_cotiza(self):
        self.connection.status = StoreConnection.Status.REVOKED
        self.connection.save(update_fields=["status"])

        response = self.client.post(self.url, data=cart(), format="json")

        self.assertEqual(response.status_code, 404)

    def test_un_carrito_ilegible_devuelve_lista_vacia_no_un_error(self):
        # Un 5xx sumaría al corta-corriente de Tiendanube y nos sacaría del
        # checkout de TODAS las tiendas, no solo de esta.
        response = self.client.post(self.url, data="no es json", content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["rates"], [])

    def test_un_carrito_sin_destino_devuelve_lista_vacia(self):
        response = self.client.post(self.url, data={"cart_id": "1"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["rates"], [])


@override_settings(**RATE_SETTINGS)
class ShippingRateApiTests(TiendanubeTestCase):
    """ABM de la tabla: lo que usa la pantalla del comerciante."""

    URL = "/api/v1/integrations/shipping-rates/"

    def payload(self, **overrides):
        data = {
            "connection": self.connection.pk,
            "option_code": "standard",
            "option_name": "Envío estándar",
            "postal_code_from": "1000",
            "postal_code_to": "1999",
            "weight_up_to_kg": "5",
            "price": "4500",
        }
        data.update(overrides)
        return data

    def test_el_dueno_carga_una_tarifa(self):
        response = self.client.post(
            self.URL, self.payload(), format="json", **auth_headers_for(self.owner)
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(ShippingRate.objects.count(), 1)

    def test_no_se_puede_cargar_una_tarifa_en_la_tienda_de_otro(self):
        intruso = make_user("intruso@example.com")

        response = self.client.post(
            self.URL, self.payload(), format="json", **auth_headers_for(intruso)
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(ShippingRate.objects.count(), 0)

    def test_el_cp_se_guarda_normalizado(self):
        self.client.post(
            self.URL,
            self.payload(postal_code_from="C1000XYZ", postal_code_to="B1999ABC"),
            format="json",
            **auth_headers_for(self.owner),
        )

        rate = ShippingRate.objects.get()
        self.assertEqual((rate.postal_code_from, rate.postal_code_to), ("1000", "1999"))

    def test_un_rango_al_reves_se_rechaza(self):
        response = self.client.post(
            self.URL,
            self.payload(postal_code_from="1999", postal_code_to="1000"),
            format="json",
            **auth_headers_for(self.owner),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("postal_code_to", response.data)

    def test_un_precio_negativo_se_rechaza(self):
        response = self.client.post(
            self.URL, self.payload(price="-10"), format="json", **auth_headers_for(self.owner)
        )

        self.assertEqual(response.status_code, 400)

    def test_un_plazo_maximo_menor_al_minimo_se_rechaza(self):
        response = self.client.post(
            self.URL,
            self.payload(delivery_days_min=5, delivery_days_max=2),
            format="json",
            **auth_headers_for(self.owner),
        )

        self.assertEqual(response.status_code, 400)

    def test_solo_se_ven_las_tarifas_propias(self):
        ShippingRate.objects.create(
            connection=self.connection,
            postal_code_from="1000",
            postal_code_to="1999",
            price=Decimal("4500"),
        )
        intruso = make_user("otro2@example.com")

        response = self.client.get(self.URL, **auth_headers_for(intruso))

        self.assertEqual(response.status_code, 200)
        results = response.data["results"] if "results" in response.data else response.data
        self.assertEqual(len(results), 0)

    def test_sin_sesion_no_se_ve_nada(self):
        self.assertEqual(self.client.get(self.URL).status_code, 401)
