"""Las URLs que pone el comerciante (WooCommerce, Magento) no pueden hacer
que el servidor le pegue a una red interna (SSRF)."""

import socket
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.integrations import safe_http
from apps.integrations.providers.base import ProviderRejectedError
from apps.integrations.providers.woocommerce.provider import WooCommerceProvider


def resolves_to(address):
    return mock.patch.object(
        socket, "getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
    )


def response(status=200, location=None):
    return mock.Mock(status_code=status, headers={"Location": location} if location else {})


@override_settings(INTEGRATIONS_ALLOW_PRIVATE_HOSTS=False)
class SafeHttpTests(SimpleTestCase):
    def test_internal_addresses_are_refused(self):
        for address in ("127.0.0.1", "10.0.0.5", "192.168.1.10", "169.254.169.254", "100.64.0.1", "::1"):
            with self.subTest(address=address), resolves_to(address), mock.patch("requests.request") as sent:
                with self.assertRaises(ProviderRejectedError) as raised:
                    safe_http.request("GET", "https://tienda.example.com/wp-json/")
                self.assertIn("red interna", str(raised.exception))
                sent.assert_not_called()

    def test_public_addresses_go_through(self):
        with resolves_to("93.184.216.34"), mock.patch("requests.request", return_value=response()) as sent:
            safe_http.request("GET", "https://tienda.example.com/wp-json/", timeout=5)
        self.assertFalse(sent.call_args.kwargs["allow_redirects"])

    def test_a_redirect_to_an_internal_address_is_refused(self):
        calls = iter([response(301, "https://interno.example.com/"), response()])
        addresses = {"tienda.example.com": "93.184.216.34", "interno.example.com": "10.0.0.1"}

        def lookup(host, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addresses[host], 443))]

        with mock.patch.object(socket, "getaddrinfo", side_effect=lookup), mock.patch(
            "requests.request", side_effect=lambda *a, **k: next(calls)
        ):
            with self.assertRaises(ProviderRejectedError):
                safe_http.request("GET", "https://tienda.example.com/wp-json/")

    def test_a_public_redirect_is_followed_without_credentials_on_another_host(self):
        calls = []

        def send(method, url, **kwargs):
            calls.append((method, url, kwargs.get("auth"), kwargs.get("headers", {}).get("Authorization")))
            return response(302, "https://www.tienda.example.com/wp-json/") if len(calls) == 1 else response()

        with resolves_to("93.184.216.34"), mock.patch("requests.request", side_effect=send):
            safe_http.request("POST", "https://tienda.example.com/wp-json/", auth=("k", "s"), headers={"Authorization": "x"})
        self.assertEqual(calls[1], ("GET", "https://www.tienda.example.com/wp-json/", None, None))

    def test_woocommerce_uses_it(self):
        with resolves_to("10.0.0.5"), mock.patch("requests.request") as sent:
            with self.assertRaises(ProviderRejectedError):
                WooCommerceProvider().request_with_keys("https://tienda.example.com", "ck", "cs", "GET", "orders")
        sent.assert_not_called()

    @override_settings(INTEGRATIONS_ALLOW_PRIVATE_HOSTS=True)
    def test_can_be_allowed_for_local_development(self):
        with resolves_to("127.0.0.1"), mock.patch("requests.request", return_value=response()) as sent:
            safe_http.request("GET", "https://localhost.example.com/")
        sent.assert_called_once()
