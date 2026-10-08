"""La VTEX simulada de los tests: atiende ``requests.request`` con las formas
de la referencia oficial (Orders API y Logistics API). Nunca sale un pedido
real."""

from unittest.mock import patch
from urllib.parse import unquote, urlparse

from apps.integrations.models import StoreConnection
from apps.integrations.providers.vtex.provider import credentials_token
from apps.integrations.tests.helpers import fake_response

ACCOUNT = "micuenta"
API = f"https://{ACCOUNT}.vtexcommercestable.com.br"
BASE_URL = "https://rotulos.example.com"
WEBHOOKS_URL = "/api/v1/integrations/vtex/webhooks/"
KEYS = ("vtexappkey-micuenta-ABCDEF", "TOKENBUENO")

VTEX_SETTINGS = {
    "INTEGRATIONS_PUBLIC_BASE_URL": BASE_URL,
    "VTEX_API_HOST_TEMPLATE": "https://{account}.vtexcommercestable.com.br",
    "VTEX_ORDERS_PAGE_SIZE": 2,
    "VTEX_RECONCILE_MINUTES": 5,
}


def vtex_order(order_id, **overrides):
    """Pedido con la forma de ``GET /api/oms/pvt/orders/{orderId}``."""
    order = {
        "orderId": order_id,
        "sequence": "502556",
        "status": "ready-for-handling",
        "creationDate": "2026-10-01T10:00:00.000000+00:00",
        "lastChange": "2026-10-01T12:00:00.7010747+00:00",
        "clientProfileData": {"email": "ana@example.com", "phone": "+5491155550000", "document": "20123456789"},
        "shippingData": {
            "address": {
                "addressType": "residential",
                "receiverName": "María Gómez",
                "postalCode": "5000",
                "city": "Córdoba",
                "state": "Córdoba",
                "country": "ARG",
                "street": "Av. Colón",
                "number": "1234",
                "neighborhood": "Centro",
                "complement": "Piso 3 B",
                "reference": "Timbre roto",
            },
            "logisticsInfo": [{"itemIndex": 0, "selectedSla": "Envío a domicilio", "deliveryChannel": "delivery"}],
            "contactInformation": [{"email": "ana@example.com", "phone": "351444"}],
        },
        "items": [{"name": "Remera", "refId": "REM-1", "quantity": 2}],
        "packageAttachment": {"packages": []},
        "paymentData": {"transactions": [{"payments": [{"cardNumber": "4111"}]}]},
    }
    order.update(overrides)
    return order


def invoice(number="0001-00001234", **overrides):
    package = {"type": "Output", "invoiceNumber": number, "courier": "", "trackingNumber": None, "trackingUrl": None}
    package.update(overrides)
    return package


class FakeVtex:
    """Simula la Orders API de una cuenta VTEX (todo pasa por ``requests.request``)."""

    def __init__(self):
        self.calls = []
        self.orders = {}
        self.feed = []
        self.committed = []
        self.feed_config = None
        self.hook_config = None
        self.valid_keys = KEYS
        self.denied = set()  # rutas a las que la clave no tiene permiso (403)
        self.hook_ping_ok = True
        self.policies = {}
        self.freights = {}  # política -> filas vigentes
        self.docks = [{"id": "1_1", "name": "Muelle principal", "freightTableIds": []}]

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        parsed = urlparse(url)
        path = unquote(parsed.path)
        self.calls.append({"method": method, "path": path, "params": dict(params or {}), "json": json, "headers": headers})
        assert f"{parsed.scheme}://{parsed.netloc}" == API, url
        if (headers.get("X-VTEX-API-AppKey"), headers.get("X-VTEX-API-AppToken")) != self.valid_keys:
            return fake_response(401, {"error": {"message": "Unauthorized"}})
        if path in self.denied:
            return fake_response(403, {"error": {"message": "Forbidden"}})

        if path == "/api/oms/pvt/orders" and method == "GET":
            per_page, page = int(params["per_page"]), int(params.get("page", 1))
            wanted = (params.get("f_status") or "").split(",")
            listed = [o for o in sorted(self.orders.values(), key=lambda o: o["creationDate"]) if not params.get("f_status") or o["status"] in wanted]
            chunk = listed[(page - 1) * per_page : page * per_page]
            pages = max(1, -(-len(listed) // per_page))
            return fake_response(200, {"list": [{"orderId": o["orderId"], "status": o["status"]} for o in chunk], "paging": {"pages": pages}})
        if path.startswith("/api/oms/pvt/orders/"):
            rest = path[len("/api/oms/pvt/orders/") :]
            order_id, _, action = rest.partition("/")
            order = self.orders.get(order_id)
            if order is None:
                return fake_response(404, {"error": {"message": "Order not found"}})
            if not action and method == "GET":
                return fake_response(200, order)
            if action == "start-handling" and method == "POST":
                order["status"] = "handling"
                return fake_response(204, None)
            if action.startswith("invoice/"):
                number = action.split("/")[1]
                package = next(p for p in order["packageAttachment"]["packages"] if p["invoiceNumber"] == number)
                if method == "PATCH":
                    package.update(json)
                    return fake_response(200, {})
                if method == "PUT" and action.endswith("/tracking"):
                    if not package.get("trackingNumber"):
                        return fake_response(400, {"error": {"message": "Invoice has no tracking number"}})
                    package["courierStatus"] = {"finished": json["isDelivered"], "data": json["events"]}
                    return fake_response(200, {})
        if path == "/api/orders/feed/config":
            if method == "GET":
                return fake_response(200, self.feed_config) if self.feed_config else fake_response(404, {})
            self.feed_config = json
            return fake_response(200, {})
        if path == "/api/orders/hook/config":
            if method == "GET":
                return fake_response(200, self.hook_config) if self.hook_config else fake_response(404, {})
            if not self.hook_ping_ok:
                return fake_response(400, {"error": {"message": "Hook endpoint did not answer the ping"}})
            self.hook_config = json
            return fake_response(200, {})
        if path == "/api/orders/feed":
            if self.feed_config is None:
                return fake_response(404, {})
            if method == "GET":
                return fake_response(200, self.feed[: int(params["maxlot"])])
            self.committed += json["handles"]
            self.feed = [item for item in self.feed if item["handle"] not in json["handles"]]
            return fake_response(204, None)
        if path.startswith("/api/logistics/pvt/shipping-policies"):
            policy_id = path.rsplit("/", 1)[-1] if path.count("/") > 4 else ""
            if method == "POST":
                self.policies[json["id"]] = json
                return fake_response(200, json)
            if policy_id not in self.policies:
                return fake_response(404, {})
            if method == "PUT":
                self.policies[policy_id] = json
            return fake_response(200, self.policies[policy_id])
        if path.startswith("/api/logistics/pvt/configuration/freights/") and path.endswith("/values/update"):
            policy_id = path.split("/")[-3]
            rows = self.freights.setdefault(policy_id, [])
            for row in json:
                plain = {key: value for key, value in row.items() if key != "operationType"}
                if row["operationType"] == 3:
                    rows.remove(plain)
                else:
                    rows.append(plain)
            return fake_response(204, None)
        if path == "/api/logistics/pvt/configuration/docks":
            return fake_response(200, self.docks)
        raise AssertionError(f"Pedido no simulado: {method} {path}")

    def calls_to(self, method, path):
        return [call for call in self.calls if call["method"] == method and call["path"] == path]


class VtexTestMixin:
    def setUp(self):
        super().setUp()
        self.vtex = FakeVtex()
        patcher = patch("apps.integrations.providers.vtex.provider.requests.request", side_effect=self.vtex.request)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _connection(self, **overrides):
        values = {
            "platform": "vtex",
            "external_store_id": ACCOUNT,
            "store_url": f"https://{ACCOUNT}.myvtex.com",
            "access_token": credentials_token(*KEYS),
        }
        values.update(overrides)
        return StoreConnection.objects.create(**values)
