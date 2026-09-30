"""Proveedor Shopify (https://shopify.dev/docs/apps).

Implementado: la CONEXIÓN de la tienda — instalación OAuth (authorization
code grant de una app no embebida), token que vence y se renueva, datos de
la tienda, webhooks (firma, lectura y registro) y desinstalación. Traer
pedidos y devolver el tracking viene después: mientras tanto
``supports_order_import`` es False y ``order_sync_events`` está vacío, así
que conectar una tienda Shopify no lanza nada que todavía no sepa hacer.

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
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.urls import reverse

from .base import (
    OAuthResult,
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


def topic_enum(event):
    """``app/uninstalled`` -> ``APP_UNINSTALLED`` (el enum de GraphQL)."""
    return event.upper().replace("/", "_")


class ShopifyProvider(StoreProvider):
    platform = "shopify"
    uninstall_events = ("app/uninstalled",)
    # Los webhooks de privacidad son obligatorios para publicar en la App
    # Store, pero NO se registran por API: se declaran en la configuración
    # de la app (Dev Dashboard). Llegan al mismo receptor de webhooks.
    privacy_events = {
        "shop/redact": "store",
        "customers/redact": "customer",
        "customers/data_request": "data_request",
    }
    supports_order_import = False
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

    def refresh_access_token(self, connection):
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
            if "ACCESS_DENIED" in codes:
                raise ProviderAuthError(f"Shopify denegó el acceso: {str(detail)[:300]}")
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

    # --- Webhooks ----------------------------------------------------------

    def verify_webhook(self, raw_body, headers):
        secret = getattr(settings, "SHOPIFY_CLIENT_SECRET", "")
        signature = get_header(headers, WEBHOOK_SIGNATURE_HEADER).strip()
        if not secret or not signature:
            return False
        digest = hmac.new(secret.encode("utf-8"), raw_body or b"", hashlib.sha256).digest()
        expected = base64.b64encode(digest)
        return hmac.compare_digest(signature.encode("utf-8"), expected)

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
