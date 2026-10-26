"""Correo Argentino y OCA en el checkout de las tiendas (``checkout.py``,
``checkout_views.py``) y despachar desde el admin de la tienda con cualquier
transportista (``store_dispatch.py``). Los transportistas se simulan."""

from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.carriers.andreani.tests.fake_andreani import AndreaniTestMixin
from apps.carriers.andreani.tests.test_andreani import make_order
from apps.carriers.checkout import checkout_for
from apps.carriers.correo_argentino.tests.fake_correo import CorreoTestMixin
from apps.carriers.models import CarrierShipment
from apps.carriers.oca.tests.fake_oca import OcaTestMixin
from apps.carriers.store_dispatch import DispatchError, carrier_for, dispatch
from apps.integrations.models import StoreConnection
from apps.integrations.providers.tiendanube import rates as tiendanube_rates
from apps.integrations.providers.woocommerce import rates as woocommerce_rates
from apps.integrations.shipping_rates import has_checkout_prices
from apps.integrations.tests.helpers import auth_headers_for, make_user
from apps.orders.models import Order
from apps.accounts.tests.team_helpers import new_business

API = "/api/v1/carriers/checkout"


def cart(postal_code="5000", grams=500, quantity=2):
    return {"destination": {"postal_code": postal_code}, "items": [{"grams": grams, "quantity": quantity}]}


class Base(AndreaniTestMixin, OcaTestMixin, CorreoTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        # Los tres simulados parchean el mismo módulo ``requests`` (``post`` OCA
        # y MiCorreo, ``request`` Andreani y Paq.ar): acá se reparte por host.
        def post(url, *args, **kwargs):
            return (self.oca.post if "ocadev" in url else self.correo.post)(url, *args, **kwargs)

        def request(method, url, *args, **kwargs):
            return (self.correo.request if "correoargentino" in url else self.andreani.request)(method, url, *args, **kwargs)

        for target, handler in (("post", post), ("request", request)):
            patcher = patch(f"requests.{target}", side_effect=handler)
            patcher.start()
            self.addCleanup(patcher.stop)
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("comercio@example.com")
        self.biz = new_business(self.user)
        self.store = StoreConnection.objects.create(
            owner=self.biz, platform="tiendanube", external_store_id="555", name="Mi tienda", access_token="t"
        )

    def enable(self, code, contract, **values):
        checkout_for(code).save_config(self.store, {"enabled": True, "contract": contract, **values})


class CheckoutTests(Base):
    def test_oca_y_correo_en_el_checkout_de_tiendanube(self):
        self.make_oca_account(self.biz)
        self.make_correo_account(self.biz)
        self.enable("oca", "64665", surcharge_percent="10")
        self.enable("correo_argentino", "CP-D", free_shipping_from="1000")

        body = tiendanube_rates.quote(self.store, dict(cart(), total_price="2000"))

        by_code = {rate["code"]: rate for rate in body["rates"]}
        self.assertEqual(set(by_code), {"oca", "correo_argentino"})
        self.assertEqual(by_code["oca"]["price"], 650.1)  # 591 + 10 %
        self.assertEqual(by_code["oca"]["price_merchant"], 591.0)
        self.assertEqual(by_code["correo_argentino"]["price"], 0.0)  # envío gratis desde 1000
        self.assertEqual(by_code["oca"]["name"], "OCA a domicilio")
        tariff = self.oca.calls_to("Tarifar_Envio_Corporativo")[0]["data"]
        self.assertEqual((tariff["CodigoPostalDestino"], tariff["PesoTotal"], tariff["CantidadPaquetes"]), ("5000", "1.000", "1"))
        rates = self.correo.calls_to("POST", "/rates")[0]["json"]
        self.assertEqual((rates["deliveredType"], rates["dimensions"]["weight"]), ("D", 1000))
        self.assertTrue(has_checkout_prices(self.store))

    def test_woocommerce_tambien_y_queda_en_cache(self):
        self.store.platform = "woocommerce"
        self.store.save()
        self.make_oca_account(self.biz)
        self.enable("oca", "64665")
        for _ in range(2):
            rates = woocommerce_rates.quote(self.store, {"postcode": "5000", "country": "AR", "weight_kg": "1.2"})
        self.assertEqual([rate["id"] if "id" in rate else rate.get("code") for rate in rates["rates"]][0:1], ["oca"])
        self.assertEqual(len(self.oca.calls_to("Tarifar_Envio_Corporativo")), 1)

    def test_una_falla_no_rompe_el_checkout_y_tambien_se_guarda(self):
        self.make_oca_account(self.biz, sender_document="")
        self.enable("oca", "64665")
        # Sin CUIT no se cotiza: la opción no aparece y no se le pregunta a OCA.
        self.assertEqual(tiendanube_rates.quote(self.store, cart())["rates"], [])
        self.assertFalse(self.oca.calls_to("Tarifar_Envio_Corporativo"))

    def test_api_lista_activa_y_prueba(self):
        self.make_oca_account(self.biz)
        response = self.client.get(f"{API}/", **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 200, response.content)
        carriers = {item["code"]: item for item in response.json()["carriers"]}
        self.assertTrue(carriers["oca"]["account_exists"])
        self.assertFalse(carriers["correo_argentino"]["account_exists"])
        self.assertEqual([item["code"] for item in carriers["oca"]["home_contracts"]], ["64665"])
        self.assertIn("oca", response.json()["stores"][0]["carriers"])

        branch = self.client.put(
            f"{API}/", {"store": self.store.pk, "carrier": "oca", "enabled": True, "contract": "62342"}, format="json", **auth_headers_for(self.user)
        )
        self.assertEqual(branch.status_code, 400)
        self.assertIn("domicilio", branch.json()["detail"])
        saved = self.client.put(
            f"{API}/",
            {"store": self.store.pk, "carrier": "oca", "enabled": True, "contract": "64665", "surcharge_amount": "100"},
            format="json",
            **auth_headers_for(self.user),
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        self.store.refresh_from_db()
        self.assertEqual(self.store.preferences["oca_checkout"]["surcharge_amount"], "100.00")

        test = self.client.post(
            f"{API}/test/", {"store": self.store.pk, "carrier": "oca", "postal_code": "X5000ABC", "weight_kg": "2"}, format="json", **auth_headers_for(self.user)
        )
        self.assertEqual(test.status_code, 200, test.content)
        self.assertEqual((test.json()["cost"], test.json()["price"]), ("591.00", "691.00"))

    def test_correo_sin_micorreo_no_se_puede_activar(self):
        self.make_correo_account(self.biz, with_micorreo=False)
        response = self.client.put(
            f"{API}/", {"store": self.store.pk, "carrier": "correo_argentino", "enabled": True, "contract": "CP-D"}, format="json", **auth_headers_for(self.user)
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("MiCorreo", response.json()["detail"])

    def test_tienda_ajena_o_transportista_inexistente(self):
        other = make_user("otro@example.com")
        new_business(other)
        response = self.client.put(f"{API}/", {"store": self.store.pk, "carrier": "oca"}, format="json", **auth_headers_for(other))
        self.assertEqual(response.status_code, 400)
        response = self.client.put(f"{API}/", {"store": self.store.pk, "carrier": "dhl"}, format="json", **auth_headers_for(self.user))
        self.assertEqual(response.status_code, 400)


@override_settings(ANDREANI_API_BASE_QA="https://apisqa.andreani.com")
class StoreDispatchTests(Base):
    def order(self, number="5001"):
        order = make_order(self.biz)
        Order.objects.filter(pk=order.pk).update(store_connection=self.store, external_number=number, external_id=number)
        order.refresh_from_db()
        return order

    def test_sin_cuentas_explica_que_hacer(self):
        with self.assertRaisesMessage(DispatchError, "Conectá tu cuenta de Andreani, Correo Argentino u OCA"):
            carrier_for(self.store)

    def test_el_unico_conectado_despacha_a_domicilio(self):
        self.make_oca_account(self.biz)
        shipments, failed = dispatch(self.store, [self.order()])
        self.assertEqual(failed, [])
        self.assertEqual(shipments[0].carrier, "oca")
        self.assertEqual(shipments[0].contract, "64665")

    def test_con_varios_usa_el_que_ofrece_el_checkout_o_pide_elegir(self):
        self.make_oca_account(self.biz)
        self.make_correo_account(self.biz)
        with self.assertRaisesMessage(DispatchError, "elegí con cuál"):
            carrier_for(self.store)
        self.enable("correo_argentino", "CP-D")
        self.assertEqual(carrier_for(self.store)[0].code, "correo_argentino")

    def test_lo_elegido_en_la_tienda_gana(self):
        self.make_oca_account(self.biz)
        self.make_account(self.biz)
        response = self.client.patch(
            f"/api/v1/integrations/stores/{self.store.pk}/settings/", {"dispatch_carrier": "oca"}, format="json", **auth_headers_for(self.user)
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["dispatch_carrier"], "oca")
        self.store.refresh_from_db()
        self.assertEqual(carrier_for(self.store)[0].code, "oca")
        bad = self.client.patch(
            f"/api/v1/integrations/stores/{self.store.pk}/settings/", {"dispatch_carrier": "dhl"}, format="json", **auth_headers_for(self.user)
        )
        self.assertEqual(bad.status_code, 400)

    def test_elegido_sin_cuenta_lista_no_cae_en_otro(self):
        self.make_oca_account(self.biz)
        self.store.preferences = {"dispatch_carrier": "correo_argentino"}
        self.store.save()
        with self.assertRaisesMessage(DispatchError, "Conectá tu cuenta de Correo Argentino"):
            carrier_for(self.store)

    def test_un_pedido_con_envio_abierto_se_imprime_el_que_tiene(self):
        self.make_oca_account(self.biz)
        order = self.order()
        first, _failed = dispatch(self.store, [order])
        again, _failed = dispatch(self.store, [order])
        self.assertEqual(first[0].pk, again[0].pk)
        self.assertEqual(CarrierShipment.objects.count(), 1)
        self.assertEqual(Decimal(first[0].quoted_price), Decimal("591.00"))
