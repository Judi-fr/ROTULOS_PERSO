"""El Magento simulado de los tests: atiende ``requests.request`` con las
formas de su API REST y verifica la firma OAuth 1.0a de cada pedido
rearmándola desde la URL que llegó de verdad. Nunca sale un pedido real."""

import base64
import hashlib
import hmac
from unittest.mock import patch
from urllib.parse import parse_qsl, quote, unquote, urlparse

from apps.integrations.models import StoreConnection
from apps.integrations.providers.magento.provider import credentials_token
from apps.integrations.tests.helpers import fake_response

SITE = "https://mitienda.com.ar"
EXTERNAL_ID = "mitienda.com.ar"
CREDS = {
    "consumer_key": "ck123",
    "consumer_secret": "cs456",
    "access_token": "at789",
    "access_token_secret": "ats012",
}
MAGENTO_SETTINGS = {"MAGENTO_ORDERS_PAGE_SIZE": 2, "MAGENTO_RECONCILE_MINUTES": 5, "INTEGRATIONS_PUBLIC_BASE_URL": ""}


def magento_order(entity_id, **overrides):
    """Pedido con la forma de ``GET /rest/V1/orders/{id}``."""
    order = {
        "entity_id": entity_id,
        "increment_id": f"0000000{entity_id}",
        "state": "processing",
        "status": "processing",
        "created_at": "2026-10-01 10:00:00",
        "updated_at": "2026-10-01 12:00:00",
        "customer_email": "ana@example.com",
        "shipping_description": "Envío a domicilio - Estándar",
        "billing_address": {
            "firstname": "Ana",
            "lastname": "López",
            "street": ["Rivadavia 100"],
            "city": "CABA",
            "region": "Ciudad Autónoma de Buenos Aires",
            "postcode": "1000",
            "country_id": "AR",
            "telephone": "1155550000",
            "email": "ana@example.com",
        },
        "items": [
            {"item_id": 1, "name": "Remera", "sku": "REM-1", "qty_ordered": 2.0, "parent_item_id": None},
            {"item_id": 2, "name": "Remera Roja M", "sku": "REM-1-R-M", "qty_ordered": 2.0, "parent_item_id": 1},
        ],
        "payment": {"cc_last4": "4111"},
        "status_histories": [],
        "extension_attributes": {
            "shipping_assignments": [
                {
                    "shipping": {
                        "method": "flatrate_flatrate",
                        "address": {
                            "firstname": "María",
                            "lastname": "Gómez",
                            "street": ["Av. Colón 1234", "Piso 3 B"],
                            "city": "Córdoba",
                            "region": "Córdoba",
                            "region_code": "X",
                            "postcode": "5000",
                            "country_id": "AR",
                            "telephone": "351444000",
                            "email": "ana@example.com",
                        },
                    }
                }
            ]
        },
    }
    order.update(overrides)
    return order


def _encode(value):
    return quote(str(value), safe="-._~")


class FakeMagento:
    """Simula la API REST de una tienda Magento. Verifica la firma OAuth 1.0a
    de cada pedido rearmándola desde la URL que llegó de verdad."""

    def __init__(self):
        self.calls = []
        self.orders = {}
        self.shipments = []
        self.comments = {}
        self.creds = dict(CREDS)
        self.rest_path = "/rest/V1/"  # "/index.php/rest/V1/" = hosting sin reescritura

    def _signature_ok(self, method, url, header):
        parsed = urlparse(url)
        oauth = dict(
            (key, unquote(value.strip('"')))
            for key, value in (part.strip().split("=", 1) for part in header[len("OAuth ") :].split(","))
        )
        if oauth.get("oauth_consumer_key") != self.creds["consumer_key"] or oauth.get("oauth_token") != self.creds["access_token"]:
            return False
        params = parse_qsl(parsed.query, keep_blank_values=True) + [(k, v) for k, v in oauth.items() if k != "oauth_signature"]
        normalized = "&".join(f"{k}={v}" for k, v in sorted((_encode(k), _encode(v)) for k, v in params))
        base = "&".join((method, _encode(f"{parsed.scheme}://{parsed.netloc}{parsed.path}"), _encode(normalized)))
        key = f"{_encode(self.creds['consumer_secret'])}&{_encode(self.creds['access_token_secret'])}"
        expected = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha256).digest()).decode()
        return hmac.compare_digest(expected, oauth.get("oauth_signature", ""))

    def request(self, method, url, json=None, headers=None, timeout=None):
        parsed = urlparse(url)
        assert f"{parsed.scheme}://{parsed.netloc}" == SITE, url
        if not parsed.path.startswith(self.rest_path):
            return fake_response(404, {"message": "Not found"})
        path = parsed.path[len(self.rest_path) :]
        query = dict(parse_qsl(parsed.query))
        self.calls.append({"method": method, "path": path, "query": query, "json": json})
        if not self._signature_ok(method, url, headers.get("Authorization", "")):
            return fake_response(401, {"message": "The consumer isn't authorized to access %resources."})

        if method == "GET" and path == "orders":
            return fake_response(200, self._search(list(self.orders.values()), query))
        if method == "GET" and path.startswith("orders/"):
            order = self.orders.get(int(path.split("/")[1]))
            return fake_response(200, order) if order else fake_response(404, {"message": "The entity that was requested doesn't exist."})
        if method == "GET" and path == "shipments":
            order_id = int(query["searchCriteria[filter_groups][0][filters][0][value]"])
            return fake_response(200, {"items": [s for s in self.shipments if s["order_id"] == order_id], "total_count": 0})
        if method == "POST" and path.startswith("order/") and path.endswith("/ship"):
            order_id = int(path.split("/")[1])
            if any(s["order_id"] == order_id for s in self.shipments):
                return fake_response(400, {"message": "Shipment Document Validation Error(s): The order does not allow a shipment to be created."})
            shipment_id = 100 + len(self.shipments)
            self.shipments.append({"entity_id": shipment_id, "order_id": order_id, "tracks": list(json.get("tracks") or []), "notified": json["notify"]})
            return fake_response(200, shipment_id)
        if method == "POST" and path == "shipment/track":
            entity = json["entity"]
            next(s for s in self.shipments if s["entity_id"] == entity["parent_id"])["tracks"].append(entity)
            return fake_response(200, {})
        if method == "POST" and path.startswith("shipment/") and path.endswith("/emails"):
            return fake_response(200, True)
        if method == "POST" and path.startswith("orders/") and path.endswith("/comments"):
            order_id = int(path.split("/")[1])
            history = json["statusHistory"]
            self.orders[order_id]["status_histories"].append(history)
            return fake_response(200, True)
        raise AssertionError(f"Pedido no simulado: {method} {path}")

    @staticmethod
    def _search(orders, query):
        field = query.get("searchCriteria[filter_groups][0][filters][0][field]")
        if field:
            value = query["searchCriteria[filter_groups][0][filters][0][value]"]
            assert query["searchCriteria[filter_groups][0][filters][0][condition_type]"] == "gteq"
            orders = [order for order in orders if order[field] >= value]
        sort = query.get("searchCriteria[sortOrders][0][field]")
        if sort:
            orders = sorted(orders, key=lambda order: order[sort])
        size, page = int(query["searchCriteria[pageSize]"]), int(query["searchCriteria[currentPage]"])
        pages = max(1, -(-len(orders) // size))
        # Como Magento: pasada la última página, repite la última.
        page = min(page, pages)
        return {"items": orders[(page - 1) * size : page * size], "total_count": len(orders)}

    def calls_to(self, method, path):
        return [call for call in self.calls if call["method"] == method and call["path"] == path]


class MagentoTestMixin:
    def setUp(self):
        super().setUp()
        self.magento = FakeMagento()
        patcher = patch("apps.integrations.providers.magento.provider.requests.request", side_effect=self.magento.request)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _connection(self, **overrides):
        values = {
            "platform": "magento",
            "external_store_id": EXTERNAL_ID,
            "store_url": SITE,
            "access_token": credentials_token(*CREDS.values()),
        }
        values.update(overrides)
        return StoreConnection.objects.create(**values)
