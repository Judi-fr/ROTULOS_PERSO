"""El Correo Argentino simulado de los tests: Paq.ar (``requests.request``) y
MiCorreo (``requests.post``) con las formas de sus manuales oficiales. Lo que
los manuales no muestran (la respuesta del alta de Paq.ar) está supuesto igual
que en el cliente: ``{"trackingNumber": ...}``."""

import base64
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

from apps.carriers.models import CarrierAccount

HOST = "https://apitest.correoargentino.com.ar"
AGREEMENT, API_KEY = "18017", "clave-paqar"
MICORREO_USER, MICORREO_PASSWORD, CUSTOMER_ID = "api-user", "api-pass", "0090000025"
PDF = b"%PDF-1.4 rotulo Correo"


def response(status_code=200, json_data=None):
    fake = MagicMock()
    fake.status_code = status_code
    fake.json.return_value = {} if json_data is None else json_data
    return fake


class FakeCorreo:
    def __init__(self):
        self.calls = []
        self.orders = []
        self.cancelled = []
        self.tracking = {}
        self.next_number = 100
        self.tokens = 0
        self.agencies = [
            {"agency_id": "SCQ", "agency_name": "SAN GENARO", "schedule": "LUN A VIE 8 A 14", "location": {"street_name": "JUAN LAZARTE", "street_number": "1498", "city_name": "SAN GENARO", "zip_code": "S2146ZAA"}},
            {"agency_id": "CBA", "agency_name": "CORDOBA CENTRO", "schedule": "LUN A VIE 8 A 18", "location": {"street_name": "COLON", "street_number": "200", "city_name": "CORDOBA", "zip_code": "X5000ABC"}},
        ]

    # --- Paq.ar ------------------------------------------------------------
    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        parsed = urlparse(url)
        assert f"{parsed.scheme}://{parsed.netloc}" == HOST, url
        path = parsed.path
        assert path.startswith("/paqar/v1/"), path
        path = path[len("/paqar/v1") :]
        self.calls.append({"api": "paqar", "method": method, "path": path, "params": params, "json": json, "headers": headers})
        if headers.get("Authorization") != f"Apikey {API_KEY}" or headers.get("agreement") != AGREEMENT:
            return response(401, {"status": 401, "error": "Unauthorized", "message": "Account not found by apiKey"})
        if method == "GET" and path == "/auth":
            return response(204)
        if method == "POST" and path == "/orders":
            order = json["order"]
            if order["deliveryType"] == "homeDelivery" and not order["shippingData"]["address"]["state"]:
                return response(400, {"status": 400, "error": "Bad request", "message": "La provincia es invalida."})
            self.orders.append(json)
            self.next_number += 1
            return response(200, {"trackingNumber": f"18017200G9AL2AC{self.next_number}"})
        if method == "POST" and path == "/labels":
            return response(
                200,
                [
                    {"trackingNumber": item["trackingNumber"], "fileBase64": base64.b64encode(PDF).decode(), "fileName": "x.pdf", "result": "OK"}
                    for item in json
                ],
            )
        if method == "GET" and path == "/tracking":
            return response(
                200,
                [{"trackingNumber": item["trackingNumber"], "event": self.tracking.get(item["trackingNumber"], [])} for item in json],
            )
        if method == "PATCH" and path.endswith("/cancel"):
            self.cancelled.append(path.split("/")[2])
            return response(200, {"codigo": 200, "mensaje": "Pedido Cancelado - No fue impuesto"})
        if method == "GET" and path == "/agencies":
            return response(200, self.agencies)
        raise AssertionError(f"Pedido no simulado: {method} {path}")

    # --- MiCorreo ----------------------------------------------------------
    def post(self, url, json=None, auth=None, headers=None, timeout=None):
        parsed = urlparse(url)
        assert f"{parsed.scheme}://{parsed.netloc}" == HOST, url
        path = parsed.path[len("/micorreo/v1") :]
        self.calls.append({"api": "micorreo", "method": "POST", "path": path, "json": json})
        if path == "/token":
            if tuple(auth or ()) != (MICORREO_USER, MICORREO_PASSWORD):
                return response(401, {"code": "401", "message": "Unauthorized"})
            self.tokens += 1
            return response(200, {"token": f"jwt-{self.tokens}", "expires": "2099-01-01 00:00:00"})
        if path == "/rates":
            if (headers or {}).get("Authorization") != f"Bearer jwt-{self.tokens}":
                return response(401, {"code": "401", "message": "Unauthorized"})
            if json["customerId"] != CUSTOMER_ID:
                return response(402, {"code": "402", "message": f"Cliente FAP no identificado {json['customerId']}"})
            price = 498.06 if json["deliveredType"] == "D" else 398.06
            return response(
                200,
                {"customerId": CUSTOMER_ID, "rates": [{"deliveredType": json["deliveredType"], "productType": "CP", "productName": "Correo Argentino Clasico", "price": price, "deliveryTimeMin": "2", "deliveryTimeMax": "5"}]},
            )
        raise AssertionError(f"Pedido no simulado: POST {path}")

    def calls_to(self, method, path):
        return [call for call in self.calls if call["method"] == method and call["path"] == path]


class CorreoTestMixin:
    def setUp(self):
        super().setUp()
        self.correo = FakeCorreo()
        for target in ("request", "post"):
            patcher = patch(f"apps.carriers.correo_argentino.client.requests.{target}", side_effect=getattr(self.correo, target))
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_correo_account(self, owner, with_micorreo=True, **overrides):
        values = {
            "owner": owner,
            "carrier": "correo_argentino",
            "environment": "qa",
            "client_code": AGREEMENT,
            "contracts": [
                {"code": "CP-D", "service": "CP", "label": "Clásico a domicilio", "kind": "home"},
                {"code": "CP-S", "service": "CP", "label": "Clásico a sucursal", "kind": "branch"},
            ],
            "sender_name": "Mi Tienda SRL",
            "sender_email": "envios@mitienda.com.ar",
            "origin_street": "Vicente Lopez",
            "origin_number": "448",
            "origin_postal_code": "B1842ZAB",
            "origin_city": "Monte Grande",
            "origin_state": "Buenos Aires",
            "extra": {"declared_value": "15000"},
        }
        values.update(overrides)
        account = CarrierAccount(**values)
        account.password = API_KEY
        if with_micorreo:
            account.extra = dict(account.extra, micorreo_user=MICORREO_USER, customer_id=CUSTOMER_ID)
            account.set_secret("micorreo_password", MICORREO_PASSWORD)
        account.save()
        return account
