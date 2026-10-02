"""Proveedor Shopify (https://shopify.dev/docs/apps).

Implementado: la conexión de la tienda — instalación OAuth (authorization
code grant de una app no embebida), token que vence y se renueva, datos de
la tienda, webhooks (firma, lectura y registro) y desinstalación — y los
PEDIDOS: importación inicial (paginada por cursor), avisos
``orders/create|updated|cancelled`` y la devolución del despacho con su
tracking (``push_fulfillment``).

**El despacho en Shopify es un "fulfillment" sobre cada fulfillment order.**
Solo se tocan los que la app puede despachar (``CREATE_FULFILLMENT`` en
``supportedActions``): los de un servicio externo (un depósito que despacha
por su cuenta) ni aparecen con nuestros scopes, y no nos corresponden.
"Entregado" NO se informa: marcarlo (``fulfillmentEventCreate``) exige el
scope ``write_fulfillments``, que la app no pide; un pedido entregado queda
en Shopify como enviado, con su tracking.

**Leer pedidos exige "protected customer data".** Sin esa aprobación en el
Dev Dashboard, Shopify responde "This app is not approved to access the
Order object" (y ``ordersCount`` da 0, que parece una tienda vacía). Solo se
piden los campos que el rótulo necesita — nombre y dirección — y NO email
ni teléfono del comprador: un rótulo nunca los imprime, y cada campo
protegido de más es algo más que Shopify revisa y que hay que custodiar.

Diferencias con Tiendanube que explican la forma de este módulo:

- **Cada tienda tiene su dominio** (``xxx.myshopify.com``) y la URL de
  autorización, el canje del código y la API cuelgan de él. Ese dominio es
  el ``external_store_id``.
- **El callback viene firmado** (``hmac`` en la query string, HMAC-SHA256
  en hex de los demás parámetros ordenados). Lo mismo cuando el comerciante
  abre la app desde su admin (``ShopifyLaunchView``).
- **El token vence a la hora.** Desde el 1/4/2026 las apps públicas nuevas
  están obligadas a usar tokens offline que vencen (``expiring=1``); desde el
  1/1/2027, todas. Se renueva con el refresh token (90 días), que rota en
  cada renovación: ver ``apps.integrations.tokens``.
- **Solo GraphQL.** Desde el 1/4/2025 las apps públicas nuevas no pueden
  usar la REST Admin API.
- **Los webhooks traen tienda y evento en headers** (``X-Shopify-Shop-Domain``,
  ``X-Shopify-Topic``) y se firman en base64 con el client secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from .addresses import split_street
from .base import (
    OWN_APP_CLIENT_ID_PREF,
    NormalizedOrder,
    OAuthResult,
    OrdersPage,
    ProviderAuthError,
    ProviderError,
    ProviderNotFoundError,
    ProviderRejectedError,
    StoreInfo,
    StoreProvider,
    WebhookMessage,
    get_header,
)

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://{shop}/admin/oauth/authorize"
TOKEN_URL = "https://{shop}/admin/oauth/access_token"
GRAPHQL_URL = "https://{shop}/admin/api/{version}/graphql.json"

# Anclado en los dos extremos: sin el "$", "x.myshopify.com.atacante.com"
# pasaría como una tienda.
SHOP_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*\.myshopify\.com$")

WEBHOOK_SIGNATURE_HEADER = "X-Shopify-Hmac-Sha256"
WEBHOOK_TOPIC_HEADER = "X-Shopify-Topic"
WEBHOOK_SHOP_HEADER = "X-Shopify-Shop-Domain"

STORE_INFO_QUERY = """
query ShopInfo {
  shop {
    name
    email
    myshopifyDomain
    primaryDomain { url }
  }
}
"""

WEBHOOKS_QUERY = """
query Webhooks {
  webhookSubscriptions(first: 100) {
    nodes { id topic uri }
  }
}
"""

# Solo lo que usa el rótulo y el estado del pedido. Nada de email/teléfono
# del comprador (ver docstring del módulo). lineItems acotado: el costo de
# una consulta GraphQL crece con first x first, y el límite es 1000 puntos.
ORDER_FIELDS = """
fragment OrderFields on Order {
  id
  legacyResourceId
  name
  createdAt
  updatedAt
  cancelledAt
  displayFulfillmentStatus
  totalWeight
  shippingLine { title }
  shippingAddress { name address1 address2 city province zip country countryCodeV2 }
  billingAddress { name }
  lineItems(first: 20) { nodes { name sku quantity } }
}
"""

ORDER_QUERY = ORDER_FIELDS + """
query Order($id: ID!) {
  order(id: $id) { ...OrderFields }
}
"""

ORDERS_QUERY = ORDER_FIELDS + """
query Orders($first: Int!, $after: String, $query: String) {
  orders(first: $first, after: $after, query: $query, sortKey: CREATED_AT) {
    nodes { ...OrderFields }
    pageInfo { hasNextPage endCursor }
  }
}
"""

# Los envíos se leen A TRAVÉS de los fulfillment orders que la app puede ver
# (los del comerciante), no desde order.fulfillments: así los de un servicio
# externo quedan afuera sin preguntar de quién son. Preguntarlo
# (Fulfillment.service) exige scopes que la app no tiene, y Shopify lo
# rechaza en cuanto el pedido tiene algún envío (confirmado en vivo).
FULFILLMENT_QUERY = """
query OrderFulfillment($id: ID!) {
  order(id: $id) {
    fulfillmentOrders(first: 20) {
      nodes {
        id status
        supportedActions { action }
        fulfillments(first: 10) {
          nodes { id status trackingInfo(first: 1) { number url company } }
        }
      }
    }
  }
}
"""

FULFILLMENT_CREATE_MUTATION = """
mutation FulfillmentCreate($fulfillment: FulfillmentInput!) {
  fulfillmentCreate(fulfillment: $fulfillment) {
    fulfillment { id status }
    userErrors { field message }
  }
}
"""

TRACKING_UPDATE_MUTATION = """
mutation FulfillmentTrackingUpdate($fulfillmentId: ID!, $trackingInfoInput: FulfillmentTrackingInput!, $notifyCustomer: Boolean) {
  fulfillmentTrackingInfoUpdate(fulfillmentId: $fulfillmentId, trackingInfoInput: $trackingInfoInput, notifyCustomer: $notifyCustomer) {
    fulfillment { id }
    userErrors { field message }
  }
}
"""

# Estados locales que se informan a Shopify como "enviado". Entregado incluido:
# si el pedido se marcó entregado sin pasar por despachado, igual tiene que
# figurar enviado en la tienda (ver docstring: "entregado" no se informa).
SHIPPED_ORDER_STATUSES = ("dispatched", "in_transit", "delivered")

# Avisos de pedido. orders/updated ya cubre pagado, preparado y despachado;
# orders/cancelled se registra aparte por las dudas de que la cancelación
# llegue sin un updated.
ORDER_SYNC_EVENTS = ("orders/create", "orders/updated", "orders/cancelled")

WEBHOOK_CREATE_MUTATION = """
mutation WebhookCreate($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
    webhookSubscription { id }
    userErrors { field message }
  }
}
"""


def _text(value):
    return str(value).strip() if value is not None else ""


def _timeout():
    return getattr(settings, "SHOPIFY_HTTP_TIMEOUT_SECONDS", 10)


def _credentials():
    client_id = getattr(settings, "SHOPIFY_CLIENT_ID", "")
    secret = getattr(settings, "SHOPIFY_CLIENT_SECRET", "")
    if not client_id or not secret:
        raise ProviderError("Faltan SHOPIFY_CLIENT_ID o SHOPIFY_CLIENT_SECRET.")
    return client_id, secret


def _json(response):
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _optional_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def query_string_hmac(params, secret):
    """HMAC que Shopify pone en ``hmac`` de las URLs que firma (callback del
    OAuth, apertura de la app): todos los demás parámetros ordenados por
    nombre como ``k=v&k=v``, HMAC-SHA256 con el client secret, en hex."""
    message = "&".join(
        f"{key}={value}" for key, value in sorted((k, v) for k, v in params.items() if k != "hmac")
    )
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def order_gid(order_id):
    order_id = _text(order_id)
    return order_id if order_id.startswith("gid://") else f"gid://shopify/Order/{order_id}"


def topic_enum(event):
    """``app/uninstalled`` -> ``APP_UNINSTALLED`` (el enum de GraphQL)."""
    return event.upper().replace("/", "_")


class ShopifyProvider(StoreProvider):
    platform = "shopify"
    order_sync_events = ORDER_SYNC_EVENTS
    uninstall_events = ("app/uninstalled",)
    # Los webhooks de privacidad son obligatorios para publicar en la App
    # Store, pero NO se registran por API: se declaran en la configuración
    # de la app (Dev Dashboard). Llegan al mismo receptor de webhooks.
    privacy_events = {
        "shop/redact": "store",
        "customers/redact": "customer",
        "customers/data_request": "data_request",
    }
    requires_shop_domain = True
    requires_oauth_state = True

    # --- Dominio de la tienda ---------------------------------------------

    def normalize_shop_domain(self, value):
        """Acepta lo que un comerciante escribiría: ``mitienda``,
        ``mitienda.myshopify.com``, con ``https://`` o con ``/admin``."""
        shop = _text(value).lower()
        shop = re.sub(r"^https?://", "", shop).split("/", 1)[0]
        if shop and "." not in shop:
            shop = f"{shop}.myshopify.com"
        if not SHOP_DOMAIN_RE.match(shop):
            raise ValueError("Ingresá el dominio de tu tienda Shopify (termina en .myshopify.com).")
        return shop

    # --- OAuth -------------------------------------------------------------

    def redirect_uri(self):
        base_url = str(getattr(settings, "INTEGRATIONS_PUBLIC_BASE_URL", "") or "").rstrip("/")
        if not base_url:
            raise ProviderError("Falta INTEGRATIONS_PUBLIC_BASE_URL (la URL pública HTTPS del backend).")
        return f"{base_url}{reverse('shopify-callback')}"

    def build_authorize_url(self, state, *, shop_domain=""):
        client_id, _ = _credentials()
        shop = self.normalize_shop_domain(shop_domain)
        params = {
            "client_id": client_id,
            "scope": getattr(settings, "SHOPIFY_SCOPES", ""),
            "redirect_uri": self.redirect_uri(),
            "state": state,
        }
        return f"{AUTHORIZE_URL.format(shop=shop)}?{urlencode(params)}"

    def verify_callback(self, query_params):
        secret = getattr(settings, "SHOPIFY_CLIENT_SECRET", "")
        received = _text(query_params.get("hmac"))
        if not secret or not received:
            return False
        params = {key: query_params.get(key) for key in query_params.keys()}
        expected = query_string_hmac(params, secret)
        return hmac.compare_digest(received.encode("utf-8"), expected.encode("ascii"))

    def callback_shop_domain(self, query_params):
        return self.normalize_shop_domain(query_params.get("shop"))

    def _token_request(self, shop, data, action):
        try:
            response = requests.post(
                TOKEN_URL.format(shop=shop),
                data=data,
                headers={"Accept": "application/json"},
                timeout=_timeout(),
            )
        except requests.RequestException as exc:
            raise ProviderError(f"No se pudo contactar a Shopify: {exc.__class__.__name__}.") from exc

        body = _json(response)
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderError(f"Shopify respondió HTTP {response.status_code} al {action}.")
        if response.status_code >= 400 or not body.get("access_token"):
            detail = body.get("error_description") or body.get("error") or f"HTTP {response.status_code}"
            raise ProviderAuthError(f"Shopify rechazó el pedido para {action}: {detail}")
        return body

    def _oauth_result(self, shop, body):
        return OAuthResult(
            access_token=str(body["access_token"]),
            external_store_id=shop,
            scopes=_text(body.get("scope")),
            refresh_token=_text(body.get("refresh_token")),
            expires_in=_optional_int(body.get("expires_in")),
            refresh_token_expires_in=_optional_int(body.get("refresh_token_expires_in")),
        )

    def exchange_code(self, code, *, shop_domain=""):
        client_id, secret = _credentials()
        shop = self.normalize_shop_domain(shop_domain)
        body = self._token_request(
            shop,
            # expiring=1: token offline que vence (obligatorio para apps
            # públicas nuevas desde el 1/4/2026).
            {"client_id": client_id, "client_secret": secret, "code": code, "expiring": "1"},
            "canjear el código de autorización",
        )
        return self._oauth_result(shop, body)

    def own_app_token(self, shop_domain, client_id, client_secret):
        """Token de la app que el propio comerciante creó en su Dev Dashboard
        e instaló en su tienda (la conexión manual): *client credentials
        grant*, sin redirecciones. Shopify solo lo permite si la app y la
        tienda son de la misma organización. El token dura 24 horas y no trae
        refresh token: se pide otro igual (``refresh_access_token``)."""
        shop = self.normalize_shop_domain(shop_domain)
        body = self._token_request(
            shop,
            {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
            "conectar con tu app",
        )
        return self._oauth_result(shop, body)

    def refresh_access_token(self, connection):
        own_client_id = (connection.preferences or {}).get(OWN_APP_CLIENT_ID_PREF)
        if own_client_id:
            return self.own_app_token(connection.external_store_id, own_client_id, connection.webhook_secret)
        client_id, secret = _credentials()
        refresh_token = connection.refresh_token
        if not refresh_token:
            raise ProviderAuthError("La tienda no tiene un refresh token guardado: hay que reinstalar la app.")
        body = self._token_request(
            connection.external_store_id,
            {
                "client_id": client_id,
                "client_secret": secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            "renovar el token",
        )
        return self._oauth_result(connection.external_store_id, body)

    # --- API -------------------------------------------------------------

    def graphql(self, connection, query, variables=None):
        """Consulta a la GraphQL Admin API de la tienda. Devuelve ``data``.

        Renueva el token si está por vencer y, si igual vuelve 401, lo
        renueva una vez más y reintenta: puede haberlo invalidado la
        plataforma antes de la hora. ``ProviderAuthError`` si ni así."""
        from ..tokens import access_token_for

        token = access_token_for(connection, self)
        response = self._post_graphql(connection, token, query, variables)
        if response.status_code == 401 and connection.token_expires_at is not None:
            token = access_token_for(connection, self, force_refresh=True)
            response = self._post_graphql(connection, token, query, variables)

        if response.status_code in (401, 403):
            raise ProviderAuthError(f"Shopify rechazó el token de la tienda (HTTP {response.status_code}).")
        if response.status_code == 404:
            raise ProviderNotFoundError("Shopify: la tienda no existe o está cerrada (HTTP 404).")
        if response.status_code == 402:
            raise ProviderError("Shopify: la tienda está congelada por falta de pago (HTTP 402).")
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderError(f"Shopify respondió HTTP {response.status_code}.")
        if response.status_code >= 400:
            raise ProviderRejectedError(f"Shopify rechazó la consulta (HTTP {response.status_code}).")

        body = _json(response)
        errors = body.get("errors")
        if errors:
            # Con GraphQL un límite de uso vuelve 200 con errors[].extensions.code.
            items = [error for error in errors if isinstance(error, dict)] if isinstance(errors, list) else []
            codes = {_text((error.get("extensions") or {}).get("code")) for error in items}
            detail = items[0].get("message") if items else errors
            if "THROTTLED" in codes:
                raise ProviderError("Shopify: se alcanzó el límite de consultas a la API.")
            # ACCESS_DENIED es un campo para el que la app no pidió el scope,
            # no un token inválido: si fuera ProviderAuthError la tienda
            # quedaría marcada "con errores" teniendo un token que sirve.
            raise ProviderRejectedError(f"Shopify rechazó la consulta: {str(detail)[:300]}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise ProviderError("Shopify devolvió una respuesta sin datos.")
        return data

    def _post_graphql(self, connection, token, query, variables):
        if not token:
            raise ProviderAuthError("La tienda no tiene un token guardado.")
        version = getattr(settings, "SHOPIFY_API_VERSION", "2026-07")
        try:
            return requests.post(
                GRAPHQL_URL.format(shop=connection.external_store_id, version=version),
                json={"query": query, "variables": variables or {}},
                headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                timeout=_timeout(),
            )
        except requests.RequestException as exc:
            raise ProviderError(f"No se pudo contactar a Shopify: {exc.__class__.__name__}.") from exc

    def get_store_info(self, connection):
        shop = self.graphql(connection, STORE_INFO_QUERY).get("shop") or {}
        primary = shop.get("primaryDomain") or {}
        store_url = _text(primary.get("url")) or f"https://{connection.external_store_id}"
        return StoreInfo(name=_text(shop.get("name")), store_url=store_url, email=_text(shop.get("email")))

    @property
    def orders_page_size(self):
        return getattr(settings, "SHOPIFY_ORDERS_PAGE_SIZE", 25)

    # --- Pedidos -----------------------------------------------------------

    def get_order(self, connection, order_id):
        order = self.graphql(connection, ORDER_QUERY, {"id": order_gid(order_id)}).get("order")
        if not isinstance(order, dict):
            raise ProviderNotFoundError(f"Shopify: el pedido {order_id} no existe.")
        return order

    def list_orders_page(self, connection, *, created_at_min="", cursor="", per_page=25):
        variables = {"first": per_page, "after": cursor or None}
        if created_at_min:
            # Solo la fecha: la sintaxis de búsqueda de Shopify se confunde
            # con el "+00:00" de un ISO completo, y un día de más no importa
            # (upsert_store_order no duplica).
            variables["query"] = f"created_at:>={created_at_min[:10]}"
        orders = self.graphql(connection, ORDERS_QUERY, variables).get("orders") or {}
        info = orders.get("pageInfo") or {}
        next_cursor = _text(info.get("endCursor")) if info.get("hasNextPage") else ""
        return OrdersPage(orders=[node for node in orders.get("nodes") or [] if isinstance(node, dict)], next_cursor=next_cursor)

    def list_orders(self, connection, *, created_at_min="", page=1, per_page=25):
        raise NotImplementedError("Shopify pagina por cursor: usar list_orders_page.")

    def normalize_order(self, raw):
        if not isinstance(raw, dict) or not (raw.get("legacyResourceId") or raw.get("id")):
            raise ValueError("El pedido de Shopify no trae 'id'.")

        address = raw.get("shippingAddress") or {}
        street, number = split_street(address.get("address1"))
        billing = raw.get("billingAddress") or {}

        items = []
        for item in ((raw.get("lineItems") or {}).get("nodes")) or []:
            if isinstance(item, dict):
                items.append(
                    {"name": _text(item.get("name")), "sku": _text(item.get("sku")), "quantity": item.get("quantity") or 1}
                )

        grams = _decimal(raw.get("totalWeight"))
        # El id numérico (legacyResourceId) y no el gid: es el que traen los
        # webhooks y los avisos de privacidad (orders_to_redact).
        external_id = _text(raw.get("legacyResourceId")) or _text(raw.get("id")).rsplit("/", 1)[-1]
        shipping_line = raw.get("shippingLine") or {}

        return NormalizedOrder(
            external_id=external_id,
            external_number=_text(raw.get("name")).lstrip("#"),
            # La dirección de envío puede venir sin nombre (un cliente cargado
            # a mano en el admin): entonces el de facturación, que es quien
            # compró. Igual que Tiendanube cae al nombre de contacto.
            recipient_name=_text(address.get("name")) or _text(billing.get("name")),
            street=street,
            number=number,
            city=_text(address.get("city")),
            state=_text(address.get("province")),
            postal_code=_text(address.get("zip")),
            country=_text(address.get("country")) or "Argentina",
            reference=_text(address.get("address2")),
            description=", ".join(f"{item['quantity']}x {item['name']}" for item in items if item["name"]),
            shipping_option=_text(shipping_line.get("title")),
            status=self._local_status(raw),
            total_weight_kg=(grams / 1000) if grams else None,
            items=items,
            external_updated_at=parse_datetime(_text(raw.get("updatedAt"))) if raw.get("updatedAt") else None,
            raw=raw,
        )

    # --- Despacho y tracking ----------------------------------------------

    def fulfillment_status_for(self, order_status):
        return "FULFILLED" if order_status in SHIPPED_ORDER_STATUSES else None

    def _mutate(self, connection, mutation, variables, key, what):
        result = self.graphql(connection, mutation, variables).get(key) or {}
        errors = result.get("userErrors") or []
        if errors:
            message = "; ".join(_text(error.get("message")) for error in errors if isinstance(error, dict))
            raise ProviderRejectedError(f"Shopify no aceptó {what}: {message[:300]}")
        return result

    def push_fulfillment(
        self, connection, order_id, *, status, tracking_code="", tracking_url="", carrier="", notify_customer=True
    ):
        """Despacha en Shopify lo que la app puede despachar y deja el
        tracking al día. Idempotente: un fulfillment order ya despachado deja
        de ofrecer ``CREATE_FULFILLMENT``, y el tracking solo se reescribe si
        cambió. Devuelve los ids de fulfillment creados o actualizados."""
        order = self.graphql(connection, FULFILLMENT_QUERY, {"id": order_gid(order_id)}).get("order")
        if not isinstance(order, dict):
            raise ProviderNotFoundError(f"Shopify: el pedido {order_id} no existe.")

        tracking = {}
        if tracking_code:
            tracking = {"number": tracking_code}
            if tracking_url:
                tracking["url"] = tracking_url
            if carrier:
                tracking["company"] = carrier

        fulfillment_orders = [
            node for node in (order.get("fulfillmentOrders") or {}).get("nodes") or [] if isinstance(node, dict)
        ]
        touched = []
        for fulfillment_order in fulfillment_orders:
            actions = {_text(item.get("action")) for item in fulfillment_order.get("supportedActions") or []}
            if "CREATE_FULFILLMENT" not in actions:
                continue
            # Uno por fulfillment order: fulfillmentCreate exige que todos
            # los de una llamada salgan de la misma ubicación.
            fulfillment = {
                "lineItemsByFulfillmentOrder": [{"fulfillmentOrderId": fulfillment_order["id"]}],
                "notifyCustomer": notify_customer,
            }
            if tracking:
                fulfillment["trackingInfo"] = tracking
            result = self._mutate(
                connection, FULFILLMENT_CREATE_MUTATION, {"fulfillment": fulfillment}, "fulfillmentCreate", "el despacho"
            )
            touched.append(_text((result.get("fulfillment") or {}).get("id")))

        if tracking:
            # Envíos ya hechos de los fulfillment orders del comerciante (los
            # recién creados arriba ya llevan el tracking y no están acá: la
            # consulta es de antes de crearlos).
            existing = {
                fulfillment["id"]: fulfillment
                for fulfillment_order in fulfillment_orders
                for fulfillment in (fulfillment_order.get("fulfillments") or {}).get("nodes") or []
                if isinstance(fulfillment, dict) and fulfillment.get("id")
            }
            for fulfillment in existing.values():
                if _text(fulfillment.get("status")).upper() != "SUCCESS":
                    continue
                current = (fulfillment.get("trackingInfo") or [{}])[:1] or [{}]
                if _text(current[0].get("number")) == tracking_code:
                    continue
                self._mutate(
                    connection,
                    TRACKING_UPDATE_MUTATION,
                    {"fulfillmentId": fulfillment["id"], "trackingInfoInput": tracking, "notifyCustomer": notify_customer},
                    "fulfillmentTrackingInfoUpdate",
                    "el número de seguimiento",
                )
                touched.append(_text(fulfillment["id"]))
        return touched

    @staticmethod
    def _local_status(raw):
        """Igual criterio que Tiendanube: cancelado, o despachado si Shopify
        ya lo marcó enviado. Cualquier otro estado no cambia el local."""
        if raw.get("cancelledAt"):
            return "cancelled"
        if _text(raw.get("displayFulfillmentStatus")).upper() == "FULFILLED":
            return "dispatched"
        return ""

    # --- Webhooks ----------------------------------------------------------

    def verify_webhook(self, raw_body, headers):
        secret = getattr(settings, "SHOPIFY_CLIENT_SECRET", "")
        signature = get_header(headers, WEBHOOK_SIGNATURE_HEADER).strip()
        if not secret or not signature:
            return False
        digest = hmac.new(secret.encode("utf-8"), raw_body or b"", hashlib.sha256).digest()
        expected = base64.b64encode(digest)
        return hmac.compare_digest(signature.encode("utf-8"), expected)

    def verify_store_webhook(self, raw_body, headers, connection):
        """Los webhooks que registramos con la app del comerciante los firma
        Shopify con el secreto de ESA app, que guardamos como
        ``webhook_secret`` de la tienda."""
        if not (connection.preferences or {}).get(OWN_APP_CLIENT_ID_PREF):
            return False
        secret = connection.webhook_secret
        signature = get_header(headers, WEBHOOK_SIGNATURE_HEADER).strip()
        if not secret or not signature:
            return False
        digest = hmac.new(secret.encode("utf-8"), raw_body or b"", hashlib.sha256).digest()
        return hmac.compare_digest(signature.encode("utf-8"), base64.b64encode(digest))

    def parse_webhook(self, raw_body, headers):
        event_type = get_header(headers, WEBHOOK_TOPIC_HEADER).strip()
        store_id = get_header(headers, WEBHOOK_SHOP_HEADER).strip().lower()
        if not event_type or not store_id:
            raise ValueError("Faltan los headers X-Shopify-Topic o X-Shopify-Shop-Domain.")
        try:
            payload = json.loads((raw_body or b"").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("El payload debe ser JSON válido.") from None
        if not isinstance(payload, dict):
            raise ValueError("El payload debe ser un objeto.")
        if event_type in self.order_sync_events:
            # El aviso trae el pedido entero, con los datos del comprador. El
            # worker lo vuelve a pedir igual (ver handlers.sync_order), así
            # que en la cola solo queda el id: nada personal guardado de más.
            payload = {"id": payload.get("id"), "updated_at": payload.get("updated_at")}
        return WebhookMessage(
            store_id=store_id,
            event_type=event_type,
            resource_id=_text(payload.get("id") or ""),
            payload=payload,
        )

    def register_webhooks(self, connection, url, events):
        data = self.graphql(connection, WEBHOOKS_QUERY)
        nodes = ((data.get("webhookSubscriptions") or {}).get("nodes")) or []
        registered = {(_text(node.get("topic")), _text(node.get("uri"))) for node in nodes if isinstance(node, dict)}

        created = []
        for event in events:
            topic = topic_enum(event)
            if (topic, url) in registered:
                continue
            result = self.graphql(
                connection,
                WEBHOOK_CREATE_MUTATION,
                {"topic": topic, "webhookSubscription": {"uri": url}},
            ).get("webhookSubscriptionCreate") or {}
            user_errors = result.get("userErrors") or []
            if user_errors:
                message = "; ".join(_text(error.get("message")) for error in user_errors if isinstance(error, dict))
                raise ProviderRejectedError(f"Shopify no registró el webhook {event}: {message[:300]}")
            created.append(event)
        return created
