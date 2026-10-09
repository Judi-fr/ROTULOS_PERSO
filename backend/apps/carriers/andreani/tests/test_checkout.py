"""El precio de Andreani en el checkout de las tiendas (``checkout.py``): se
suma a la tabla de tarifas en Tiendanube y WooCommerce, con la cuenta del dueño
de la tienda, y nunca le rompe el checkout a nadie. Andreani se simula."""

from decimal import Decimal

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.carriers.andreani import checkout
from apps.integrations.models import ShippingRate, StoreConnection
from apps.integrations.providers.tiendanube import labels as store_labels
from apps.integrations.providers.tiendanube import rates as tiendanube_rates
from apps.integrations.providers.woocommerce import rates as woocommerce_rates
from apps.integrations.shipping_rates import has_checkout_prices
from apps.integrations.tests.helpers import auth_headers_for, make_user

from .fake_andreani import AndreaniTestMixin

API = "/api/v1/carriers/andreani"


def cart(postal_code="5000", grams=500, quantity=2):
    return {"destination": {"postal_code": postal_code}, "items": [{"grams": grams, "quantity": quantity}]}


class CheckoutTestCase(AndreaniTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("comercio@example.com")
        self.account = self.make_account(self.user)
        self.store = StoreConnection.objects.create(
            owner=self.user, platform="tiendanube", external_store_id="555", name="Mi tienda", access_token="t"
        )

    def enable(self, store=None, **values):
        data = {"enabled": True, "contract": "400006709", "name": "Andreani a domicilio"}
        data.update(values)
        checkout.save_config(store or self.store, data)

    def quotes(self):
        return self.andreani.calls_to("GET", "/v1/tarifas")


class CheckoutQuoteTests(CheckoutTestCase):
    def test_tiendanube_ofrece_andreani_ademas_de_la_tabla(self):
        ShippingRate.objects.create(
            connection=self.store, postal_code_from="5000", postal_code_to="5999", price=Decimal("3000"), option_code="standard"
        )
        self.enable(delivery_days_min=2, delivery_days_max=4)

        body = tiendanube_rates.quote(self.store, cart())

        by_code = {rate["code"]: rate for rate in body["rates"]}
        self.assertEqual(set(by_code), {"standard", "andreani"})
        # 1 kg x 1000 x 1,21: lo que paga el comprador es con IVA.
        self.assertEqual(by_code["andreani"]["price"], 1210.0)
        self.assertEqual(by_code["andreani"]["name"], "Andreani a domicilio")
        self.assertIn("max_delivery_date", by_code["andreani"])
        params = self.quotes()[0]["params"]
        self.assertEqual((params["cpDestino"], params["contrato"], params["cliente"]), ("5000", "400006709", "CL0001"))
        # Un tiempo de espera corto: está en medio de una venta.
        self.assertEqual(self.andreani.last_timeout, 4)

    def test_sin_tabla_andreani_solo_y_la_respuesta_queda_en_cache(self):
        self.enable()
        first = tiendanube_rates.quote(self.store, cart())
        # 1,1 kg redondea a la misma franja de medio kilo (1,5): una sola consulta más.
        tiendanube_rates.quote(self.store, cart(grams=550))
        tiendanube_rates.quote(self.store, cart(grams=550))

        self.assertEqual([rate["code"] for rate in first["rates"]], ["andreani"])
        self.assertEqual(len(self.quotes()), 2)
        self.assertEqual(self.quotes()[1]["params"]["bultos[0][kilos]"], "1.5")

    def test_carrito_sin_pesos_cotiza_el_paquete_por_defecto_de_la_cuenta(self):
        self.enable()
        tiendanube_rates.quote(self.store, cart(grams=0))
        self.assertEqual(self.quotes()[0]["params"]["bultos[0][kilos]"], "1.0")

    def test_andreani_caida_no_saca_la_tabla_y_no_se_insiste_en_cada_checkout(self):
        ShippingRate.objects.create(
            connection=self.store, postal_code_from="5000", postal_code_to="5999", price=Decimal("3000"), option_code="standard"
        )
        self.enable()
        self.andreani.quote_down = True

        body = tiendanube_rates.quote(self.store, cart())
        tiendanube_rates.quote(self.store, cart())

        self.assertEqual([rate["code"] for rate in body["rates"]], ["standard"])
        self.assertEqual(len(self.quotes()), 1)

    def test_desactivado_o_sin_cuenta_no_consulta_a_andreani(self):
        self.assertEqual(tiendanube_rates.quote(self.store, cart())["rates"], [])
        self.enable(enabled=False)
        self.assertEqual(tiendanube_rates.quote(self.store, cart())["rates"], [])
        self.enable()
        self.account.is_active = False
        self.account.save()
        self.assertEqual(tiendanube_rates.quote(self.store, cart())["rates"], [])
        self.assertFalse(self.quotes())

    def test_la_tabla_gana_si_ya_usa_el_codigo_andreani(self):
        ShippingRate.objects.create(
            connection=self.store, postal_code_from="5000", postal_code_to="5000", price=Decimal("999"), option_code="andreani"
        )
        self.enable()
        body = tiendanube_rates.quote(self.store, cart())
        self.assertEqual([rate["price"] for rate in body["rates"]], [999.0])

    def test_woocommerce_tambien(self):
        woo = StoreConnection.objects.create(
            owner=self.user, platform="woocommerce", external_store_id="https://tienda.example.com", name="Woo", access_token="{}"
        )
        self.enable(store=woo)
        body = woocommerce_rates.quote(woo, {"postcode": "5000", "country": "AR", "weight_kg": "2"})
        self.assertEqual(body["rates"][0]["code"], "andreani")
        self.assertEqual(body["rates"][0]["price"], "2420.00")

    def test_con_andreani_activado_se_puede_dar_de_alta_el_carrier_sin_tabla(self):
        self.assertFalse(has_checkout_prices(self.store))
        self.enable()
        self.assertTrue(has_checkout_prices(self.store))


@override_settings(INTEGRATIONS_PUBLIC_BASE_URL="https://rotulos.example.com")
class CheckoutCallbackTests(CheckoutTestCase):
    def test_el_callback_de_tiendanube_contesta_con_andreani(self):
        self.enable()
        token = store_labels.make_callback_token(self.store)
        response = self.client.post(f"/api/v1/integrations/tiendanube/rates/{token}", cart(), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rates"][0]["code"], "andreani")


class CheckoutSettingsTests(CheckoutTestCase):
    def test_lista_solo_las_tiendas_que_preguntan_el_precio(self):
        StoreConnection.objects.create(owner=self.user, platform="vtex", external_store_id="micuenta", access_token="{}")
        StoreConnection.objects.create(owner=make_user("otro@example.com"), platform="tiendanube", external_store_id="9", access_token="t")
        response = self.client.get(f"{API}/checkout/", **auth_headers_for(self.user))
        self.assertEqual([item["store"] for item in response.data["results"]], [self.store.pk])
        self.assertFalse(response.data["results"][0]["enabled"])

    def test_activar_con_un_contrato_a_domicilio(self):
        response = self.client.put(
            f"{API}/checkout/",
            {"store": self.store.pk, "enabled": True, "contract": "400006709", "name": "Andreani", "delivery_days_max": 5},
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.store.refresh_from_db()
        self.assertEqual(checkout.config(self.store)["contract"], "400006709")
        self.assertTrue(response.data["enabled"])

    def test_no_se_activa_con_un_contrato_de_sucursal_ni_sin_codigo_de_cliente(self):
        branch = self.client.put(
            f"{API}/checkout/", {"store": self.store.pk, "enabled": True, "contract": "400006710"}, format="json", **auth_headers_for(self.user)
        )
        self.assertEqual(branch.status_code, 400)
        self.assertIn("domicilio", branch.data["detail"])
        self.account.client_code = ""
        self.account.save()
        no_client = self.client.put(
            f"{API}/checkout/", {"store": self.store.pk, "enabled": True, "contract": "400006709"}, format="json", **auth_headers_for(self.user)
        )
        self.assertIn("código de cliente", no_client.data["detail"])
        self.assertEqual(checkout.config(self.store), {})

    def test_la_tienda_de_otro_no_se_toca(self):
        other = make_user("otro@example.com")
        response = self.client.put(
            f"{API}/checkout/", {"store": self.store.pk, "enabled": False}, format="json", **auth_headers_for(other)
        )
        self.assertEqual(response.status_code, 400)

    def test_probar_cotiza_sin_cache(self):
        self.enable()
        for _ in range(2):
            response = self.client.post(
                f"{API}/checkout/test/", {"store": self.store.pk, "postal_code": "X5000ABC", "weight_kg": "2"}, format="json", **auth_headers_for(self.user)
            )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["price"], response.data["postal_code"]), ("2420.00", "5000"))
        self.assertEqual(len(self.quotes()), 2)


class SurchargeAndFreeShippingTests(CheckoutTestCase):
    def test_el_recargo_se_suma_y_la_tienda_paga_el_costo(self):
        self.enable(surcharge_percent="10", surcharge_amount="500")
        body = tiendanube_rates.quote(self.store, cart())
        rate = body["rates"][0]
        # 1210 + 10 % + 500
        self.assertEqual(rate["price"], 1831.0)
        self.assertEqual(rate["price_merchant"], 1210.0)

    def test_envio_gratis_desde_un_monto_con_el_total_de_tiendanube(self):
        self.enable(surcharge_amount="500", free_shipping_from="20000")
        cheap = tiendanube_rates.quote(self.store, dict(cart(), total_price=19999.99))
        free = tiendanube_rates.quote(self.store, dict(cart(), total_price=20000))
        self.assertEqual(cheap["rates"][0]["price"], 1710.0)
        self.assertEqual((free["rates"][0]["price"], free["rates"][0]["price_merchant"]), (0.0, 1210.0))
        # Un solo pedido a Andreani: la caché guarda el costo, no el precio final.
        self.assertEqual(len(self.quotes()), 1)

    def test_sin_total_price_se_suman_los_items(self):
        self.enable(free_shipping_from="1000")
        payload = {"destination": {"postal_code": "5000"}, "items": [{"grams": 500, "quantity": 2, "price": 600}]}
        self.assertEqual(tiendanube_rates.quote(self.store, payload)["rates"][0]["price"], 0.0)

    def test_sin_total_no_hay_envio_gratis(self):
        self.enable(free_shipping_from="0")
        self.assertEqual(tiendanube_rates.quote(self.store, cart())["rates"][0]["price"], 1210.0)

    def test_woocommerce_con_el_total_del_plugin_y_sin_el(self):
        woo = StoreConnection.objects.create(
            owner=self.user, platform="woocommerce", external_store_id="https://tienda.example.com", name="Woo", access_token="{}"
        )
        self.enable(store=woo, free_shipping_from="5000")
        new_plugin = woocommerce_rates.quote(woo, {"postcode": "5000", "country": "AR", "weight_kg": "1", "cart_total": "5000"})
        old_plugin = woocommerce_rates.quote(woo, {"postcode": "5000", "country": "AR", "weight_kg": "1"})
        self.assertEqual(new_plugin["rates"][0]["price"], "0.00")
        self.assertEqual(old_plugin["rates"][0]["price"], "1210.00")

    def test_la_tabla_no_cambia(self):
        ShippingRate.objects.create(
            connection=self.store, postal_code_from="5000", postal_code_to="5999", price=Decimal("3000"), option_code="standard"
        )
        self.enable(surcharge_amount="500", free_shipping_from="1")
        by_code = {rate["code"]: rate for rate in tiendanube_rates.quote(self.store, dict(cart(), total_price=100))["rates"]}
        self.assertEqual((by_code["standard"]["price"], by_code["standard"]["price_merchant"]), (3000.0, 3000.0))

    def test_guardar_valida_y_probar_muestra_costo_y_precio(self):
        bad = self.client.put(
            f"{API}/checkout/",
            {"store": self.store.pk, "enabled": True, "contract": "400006709", "surcharge_percent": "-5"},
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(bad.status_code, 400)
        self.assertIn("surcharge_percent", bad.data)
        ok = self.client.put(
            f"{API}/checkout/",
            {"store": self.store.pk, "enabled": True, "contract": "400006709", "surcharge_percent": "10", "free_shipping_from": "30000"},
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual((ok.data["surcharge_percent"], ok.data["free_shipping_from"]), ("10.00", "30000.00"))
        test = self.client.post(
            f"{API}/checkout/test/",
            {"store": self.store.pk, "postal_code": "5000", "weight_kg": "1", "cart_total": "1000"},
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual((test.data["cost"], test.data["price"]), ("1210.00", "1331.00"))
        free = self.client.post(
            f"{API}/checkout/test/",
            {"store": self.store.pk, "postal_code": "5000", "weight_kg": "1", "cart_total": "30000"},
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(free.data["price"], "0.00")
