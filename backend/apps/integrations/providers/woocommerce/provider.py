"""Proveedor WooCommerce (https://woocommerce.github.io/woocommerce-rest-api-docs/).

Cada tienda es un WordPress propio: no hay una tienda de apps central ni un
OAuth de la plataforma. Lo que cambia respecto de Tiendanube y Shopify:

- **Las credenciales son claves REST** (consumer key + secret) que el
  comerciante genera en SU sitio, por dos caminos: la autorización
  automática (``/wc-auth/v1/authorize``: aprueba en su admin y WooCommerce nos
  POSTea las claves, ver ``views.WooCommerceKeysView``) o pegándolas a mano
  (``views.WooCommerceManualConnectView``). No vencen. Se guardan las dos,
  cifradas, en ``access_token`` (JSON: ``{"key", "secret"}``).
- **Solo HTTPS.** Por HTTP la API exige firmar cada pedido con OAuth 1.0a;
  no se implementa: una tienda que cobra sin HTTPS no es el caso a cubrir.
  Con HTTPS, Basic auth; hay hostings que se comen el header Authorization,
  así que ante un 401 se reintenta con las claves en la query string y se
  recuerda (``preferences["woo_auth"] = "query"``).
- **Los webhooks los creamos nosotros**, con un secreto POR TIENDA
  (``StoreConnection.webhook_secret``), y llegan a
  ``woocommerce/webhooks/?store=<id>``: la firma (base64 de HMAC-SHA256 del
  body) se verifica con el secreto de esa tienda. Al crearlos, WooCommerce
  manda un "ping" (``webhook_id=N``, sin topic) que solo hay que contestar 200.
- **Los webhooks no alcanzan.** WooCommerce desactiva uno tras 5 entregas
  fallidas y los dispara un cron de WordPress que en sitios con poco tráfico
  corre tarde. Por eso ``supports_reconciliation``: el worker repasa cada
  tanto los pedidos modificados y vuelve a activar los webhooks.
- **No hay aviso de desinstalación**: si borran las claves, la API contesta
  401 y la tienda queda "con errores" (``handlers._call_api``).
- **No hay campo de seguimiento**: despachar pasa el pedido a "completed" y
  agrega una nota visible para el cliente con el correo y el número
  (decisión del 2026-10-01; sin plugins de tracking).
- **La provincia llega como código** (``C``, ``B``...): se traduce a nombre.
- Del comprador solo se guarda lo que necesita el rótulo: ni email ni
  teléfono (igual que Shopify).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from django.utils.dateparse import parse_datetime

from ..addresses import split_street
from ..base import (
    NormalizedOrder,
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

API_PATH = "/wp-json/wc/v3/"
AUTHORIZE_PATH = "/wc-auth/v1/authorize"

WEBHOOK_SIGNATURE_HEADER = "X-WC-Webhook-Signature"
WEBHOOK_TOPIC_HEADER = "X-WC-Webhook-Topic"
WEBHOOK_SOURCE_HEADER = "X-WC-Webhook-Source"

ORDER_SYNC_EVENTS = ("order.created", "order.updated")

# Códigos de provincia de WooCommerce para Argentina (los de ISO 3166-2:AR).
AR_STATES = {
    "C": "Ciudad Autónoma de Buenos Aires",
    "B": "Buenos Aires",
    "K": "Catamarca",
    "H": "Chaco",
    "U": "Chubut",
    "X": "Córdoba",
    "W": "Corrientes",
    "E": "Entre Ríos",
    "P": "Formosa",
    "Y": "Jujuy",
    "L": "La Pampa",
    "F": "La Rioja",
    "M": "Mendoza",
    "N": "Misiones",
    "Q": "Neuquén",
    "R": "Río Negro",
    "A": "Salta",
    "J": "San Juan",
    "D": "San Luis",
    "Z": "Santa Cruz",
    "S": "Santa Fe",
    "G": "Santiago del Estero",
    "V": "Tierra del Fuego",
    "T": "Tucumán",
}
COUNTRY_NAMES = {"AR": "Argentina"}

# Estados de WooCommerce -> estado local. "completed" es lo que ponemos al
# despachar (ver push_fulfillment), así que se lee como despachado.
CANCELLED_STATUSES = ("cancelled", "refunded", "failed")
FINISHED_STATUS = "completed"

SHIPPED_ORDER_STATUSES = ("dispatched", "in_transit", "delivered")

SITE_URL_RE = re.compile(r"^[a-z0-9]([a-z0-9\-\.]*[a-z0-9])?(:\d+)?(/[^\s?#]*)?$")


def _text(value):
    return str(value).strip() if value is not None else ""


def _timeout():
    return getattr(settings, "WOOCOMMERCE_HTTP_TIMEOUT_SECONDS", 15)


def _json(response):
    try:
        return response.json()
    except ValueError:
        return {}


def external_store_id_for(site_url):
    """``https://MiTienda.com/tienda/`` -> ``mitienda.com/tienda``."""
    parsed = urlparse(site_url)
    return f"{(parsed.netloc or '').lower()}{parsed.path.rstrip('/')}"


def credentials(connection):
    """``(consumer_key, consumer_secret)`` guardados de la tienda."""
    try:
        data = json.loads(connection.access_token or "{}")
    except ValueError:
        data = {}
    key, secret = _text(data.get("key")), _text(data.get("secret"))
    if not key or not secret:
        raise ProviderAuthError("La tienda no tiene claves de la API guardadas: hay que volver a conectarla.")
    return key, secret


def credentials_token(key, secret):
    """Lo que se guarda en ``access_token`` (cifrado por el modelo)."""
    return json.dumps({"key": key, "secret": secret})


def valid_key_pair(key, secret):
    return _text(key).startswith("ck_") and _text(secret).startswith("cs_")


def _remember_print_plugin(connection, linked):
    """``preferences["print_plugin"]``: si el plugin quedó vinculado, para
    mostrarlo en la tienda (``tiendas.html``). Se escribe con un UPDATE y no
    con ``save()``: el worker trae copias de la conexión que pueden ser
    viejas."""
    if (connection.preferences or {}).get("print_plugin") != linked:
        connection.preferences = dict(connection.preferences or {}, print_plugin=linked)
        type(connection).objects.filter(pk=connection.pk).update(preferences=connection.preferences)


class WooCommerceProvider(StoreProvider):
    platform = "woocommerce"
    quotes_at_checkout = True  # el plugin pregunta: rates.py
    order_sync_events = ORDER_SYNC_EVENTS
    requires_shop_domain = True
    requires_oauth_state = True
    uses_authorization_code = False
    webhook_secret_per_store = True
    supports_reconciliation = True

    @property
    def orders_page_size(self):
        return getattr(settings, "WOOCOMMERCE_ORDERS_PAGE_SIZE", 50)

    # --- Sitio -------------------------------------------------------------

    def normalize_shop_domain(self, value):
        """La URL base del sitio, ``https://host[/ruta]`` sin barra final.
        Acepta ``mitienda.com`` o la URL pegada del admin. ``ValueError`` si
        no es una URL o es HTTP."""
        raw = _text(value)
        if raw.lower().startswith("http://"):
            raise ValueError("La tienda tiene que usar HTTPS (https://...).")
        raw = re.sub(r"^https://", "", raw, flags=re.IGNORECASE)
        raw = raw.split("?", 1)[0].split("#", 1)[0]
        # La URL pegada desde el admin trae /wp-admin/...: el sitio es lo de antes.
        raw = re.split(r"/wp-(?:admin|json|login)", raw, maxsplit=1)[0].rstrip("/")
        host, _, path = raw.partition("/")
        candidate = f"{host.lower()}{'/' + path if path else ''}"
        if not raw or "." not in host or not SITE_URL_RE.match(candidate):
            raise ValueError("Ingresá la dirección de tu tienda (por ejemplo, https://mitienda.com).")
        return f"https://{candidate}"

    # --- Autorización automática (wc-auth) ---------------------------------

    def _public_base_url(self):
        base_url = str(getattr(settings, "INTEGRATIONS_PUBLIC_BASE_URL", "") or "").rstrip("/")
        if not base_url:
            raise ProviderError("Falta INTEGRATIONS_PUBLIC_BASE_URL (la URL pública HTTPS del backend).")
        return base_url

    def build_authorize_url(self, state, *, shop_domain=""):
        """``state`` viaja como ``user_id`` (WooCommerce lo devuelve tal cual en
        el POST de las claves y en la vuelta del navegador)."""
        site = self.normalize_shop_domain(shop_domain)
        base_url = self._public_base_url()
        params = {
            "app_name": getattr(settings, "WOOCOMMERCE_APP_NAME", "Rotulos perso"),
            "scope": "read_write",
            "user_id": state,
            "return_url": f"{base_url}{reverse('woocommerce-return')}",
            "callback_url": f"{base_url}{reverse('woocommerce-keys')}",
        }
        return f"{site}{AUTHORIZE_PATH}?{urlencode(params)}"

    # --- API ---------------------------------------------------------------

    def _send(self, method, url, key, secret, mode, params, json_body):
        params = dict(params or {})
        auth = None
        if mode == "query":
            params.update({"consumer_key": key, "consumer_secret": secret})
        else:
            auth = (key, secret)
        try:
            return requests.request(
                method,
                url,
                params=params or None,
                json=json_body,
                auth=auth,
                headers={"Accept": "application/json"},
                timeout=_timeout(),
            )
        except requests.RequestException as exc:
            raise ProviderError(f"No se pudo contactar a la tienda: {exc.__class__.__name__}.") from exc

    def request_with_keys(self, site_url, key, secret, method, path, *, params=None, json_body=None, mode="basic"):
        """Pedido a la API con claves explícitas (sin conexión guardada todavía,
        p. ej. al validar las que pegó el comerciante). Devuelve
        ``(response, mode)``: el modo de autenticación que funcionó."""
        url = f"{site_url.rstrip('/')}{API_PATH}{path.lstrip('/')}"
        response = self._send(method, url, key, secret, mode, params, json_body)
        if response.status_code == 401 and mode == "basic":
            # Hostings que descartan el header Authorization: las mismas claves
            # por query string (solo HTTPS, que ya es requisito).
            retry = self._send(method, url, key, secret, "query", params, json_body)
            if retry.status_code != 401:
                return retry, "query"
        return response, mode

    def api_request(self, connection, method, path, *, params=None, json_body=None):
        key, secret = credentials(connection)
        mode = (connection.preferences or {}).get("woo_auth") or "basic"
        response, used_mode = self.request_with_keys(
            connection.store_url, key, secret, method, path, params=params, json_body=json_body, mode=mode
        )
        if used_mode != mode:
            connection.preferences = dict(connection.preferences or {}, woo_auth=used_mode)
            type(connection).objects.filter(pk=connection.pk).update(preferences=connection.preferences)
        return self._check(response, method, path)

    def _check(self, response, method, path):
        label = f"{method} /{path.lstrip('/')}"
        if response.status_code in (401, 403):
            raise ProviderAuthError(
                f"La tienda rechazó las claves de la API (HTTP {response.status_code}): "
                "puede que las hayan borrado o que no tengan permiso de lectura y escritura."
            )
        if response.status_code == 404:
            raise ProviderNotFoundError(f"WooCommerce: no existe {label} (HTTP 404).")
        if response.status_code in (400, 422):
            body = _json(response)
            detail = body.get("message") if isinstance(body, dict) else ""
            raise ProviderRejectedError(
                f"WooCommerce rechazó {label} (HTTP {response.status_code})" + (f": {str(detail)[:300]}" if detail else ".")
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderError(f"La tienda respondió HTTP {response.status_code} en {label}.")
        if response.status_code >= 400:
            raise ProviderRejectedError(f"WooCommerce rechazó {label} (HTTP {response.status_code}).")
        return response

    def check_credentials(self, site_url, key, secret):
        """Valida claves pegadas a mano antes de guardarlas: tienen que poder
        leer pedidos. Devuelve el modo de autenticación que funcionó.
        ``ProviderAuthError`` si la tienda las rechaza."""
        response, mode = self.request_with_keys(site_url, key, secret, "GET", "orders", params={"per_page": 1})
        if response.status_code == 404:
            raise ProviderNotFoundError(
                "No encontramos WooCommerce en esa dirección. Revisá que sea la de tu tienda y que tenga los "
                "enlaces permanentes activados."
            )
        self._check(response, "GET", "orders")
        return mode

    def get_store_info(self, connection):
        """Nombre del sitio desde la raíz pública de la API de WordPress
        (``/wp-json/``), que no necesita claves."""
        try:
            response = requests.get(
                f"{connection.store_url.rstrip('/')}/wp-json/", headers={"Accept": "application/json"}, timeout=_timeout()
            )
        except requests.RequestException as exc:
            raise ProviderError(f"No se pudo contactar a la tienda: {exc.__class__.__name__}.") from exc
        body = _json(response) if response.status_code < 400 else {}
        body = body if isinstance(body, dict) else {}
        return StoreInfo(name=_text(body.get("name")), store_url=connection.store_url)

    # --- Pedidos -----------------------------------------------------------

    def get_order(self, connection, order_id):
        data = _json(self.api_request(connection, "GET", f"orders/{order_id}"))
        if not isinstance(data, dict) or not data:
            raise ProviderError("WooCommerce devolvió el pedido vacío o con un formato inesperado.")
        return data

    def _orders_page(self, connection, params, cursor, per_page):
        page = max(int(cursor or 1), 1)
        response = self.api_request(
            connection, "GET", "orders", params=dict(params, page=page, per_page=per_page, orderby="date", order="asc")
        )
        orders = _json(response)
        if not isinstance(orders, list):
            raise ProviderError("WooCommerce devolvió la lista de pedidos con un formato inesperado.")
        try:
            total_pages = int(response.headers.get("X-WP-TotalPages") or 0)
        except ValueError:
            total_pages = 0
        has_next = page < total_pages if total_pages else len(orders) >= per_page
        return OrdersPage(orders=[order for order in orders if isinstance(order, dict)], next_cursor=str(page + 1) if has_next else "")

    def list_orders_page(self, connection, *, created_at_min="", cursor="", per_page=50):
        params = {}
        if created_at_min:
            # Fechas en UTC: sin dates_are_gmt, WooCommerce las compara con la
            # hora local del sitio.
            params = {"after": created_at_min[:19], "dates_are_gmt": "true"}
        return self._orders_page(connection, params, cursor, per_page)

    def list_updated_orders_page(self, connection, *, updated_after, cursor="", per_page=50):
        return self._orders_page(
            connection, {"modified_after": updated_after[:19], "dates_are_gmt": "true"}, cursor, per_page
        )

    def normalize_order(self, raw):
        if not isinstance(raw, dict) or raw.get("id") in (None, ""):
            raise ValueError("El pedido de WooCommerce no trae 'id'.")

        shipping = raw.get("shipping") or {}
        billing = raw.get("billing") or {}
        # Un pedido sin envío (digital, retiro) no trae dirección de envío: se
        # usa la de facturación para no dejar el rótulo vacío.
        address = shipping if _text(shipping.get("address_1")) else billing

        def full_name(data):
            return " ".join(part for part in (_text(data.get("first_name")), _text(data.get("last_name"))) if part)

        country_code = _text(address.get("country")).upper()
        state_code = _text(address.get("state"))
        state = AR_STATES.get(state_code.upper(), state_code) if country_code == "AR" else state_code
        street, number = split_street(address.get("address_1"))

        items = []
        for item in raw.get("line_items") or []:
            if isinstance(item, dict):
                items.append({"name": _text(item.get("name")), "sku": _text(item.get("sku")), "quantity": item.get("quantity") or 1})

        shipping_lines = [line for line in raw.get("shipping_lines") or [] if isinstance(line, dict)]
        modified = _text(raw.get("date_modified_gmt"))

        # En la copia guardada no queda el email ni el teléfono del comprador.
        stored = dict(raw)
        stored["billing"] = {k: v for k, v in billing.items() if k not in ("email", "phone")}
        stored["shipping"] = {k: v for k, v in shipping.items() if k != "phone"}

        return NormalizedOrder(
            external_id=_text(raw.get("id")),
            external_number=_text(raw.get("number")) or _text(raw.get("id")),
            recipient_name=full_name(shipping) or full_name(billing),
            street=street,
            number=number,
            city=_text(address.get("city")),
            state=state,
            postal_code=_text(address.get("postcode")),
            country=COUNTRY_NAMES.get(country_code, country_code) or "Argentina",
            reference=_text(address.get("address_2")),
            description=", ".join(f"{item['quantity']}x {item['name']}" for item in items if item["name"]),
            shipping_option=_text(shipping_lines[0].get("method_title")) if shipping_lines else "",
            status=self._local_status(raw),
            items=items,
            external_updated_at=parse_datetime(f"{modified}+00:00") if modified else None,
            raw=stored,
        )

    @staticmethod
    def _local_status(raw):
        status = _text(raw.get("status")).lower()
        if status in CANCELLED_STATUSES:
            return "cancelled"
        if status == FINISHED_STATUS:
            return "dispatched"
        return ""

    # --- Despacho y tracking ----------------------------------------------

    def fulfillment_status_for(self, order_status):
        return FINISHED_STATUS if order_status in SHIPPED_ORDER_STATUSES else None

    @staticmethod
    def tracking_note(tracking_code, tracking_url="", carrier=""):
        parts = ["Tu pedido fue despachado"]
        parts[0] += f" por {carrier}." if carrier else "."
        parts.append(f"Número de seguimiento: {tracking_code}.")
        if tracking_url:
            parts.append(f"Seguí tu envío en: {tracking_url}")
        return " ".join(parts)

    def push_fulfillment(
        self, connection, order_id, *, status, tracking_code="", tracking_url="", carrier="", notify_customer=True
    ):
        """Pasa el pedido a "completed" (si no lo está ni fue cancelado) y, con
        tracking, agrega una nota para el cliente. Idempotente: la nota con
        ese número no se repite. Devuelve lo que hizo."""
        order = self.get_order(connection, order_id)
        done = []

        if tracking_code:
            notes = _json(self.api_request(connection, "GET", f"orders/{order_id}/notes", params={"type": "customer"}))
            already = any(
                isinstance(note, dict) and tracking_code in _text(note.get("note")) for note in (notes if isinstance(notes, list) else [])
            )
            if not already:
                self.api_request(
                    connection,
                    "POST",
                    f"orders/{order_id}/notes",
                    json_body={"note": self.tracking_note(tracking_code, tracking_url, carrier), "customer_note": notify_customer},
                )
                done.append("note")

        current = _text(order.get("status")).lower()
        if current not in (FINISHED_STATUS, *CANCELLED_STATUSES):
            self.api_request(connection, "PUT", f"orders/{order_id}", json_body={"status": status})
            done.append("status")
        return done

    # --- Webhooks ----------------------------------------------------------

    def ensure_webhook_secret(self, connection):
        """El secreto se crea una sola vez, leyéndolo de la base con la fila
        bloqueada: el worker carga las conexiones de toda una tanda de eventos
        de una vez, y una copia vieja sin secreto generaría otro distinto del
        que ya tienen los webhooks de la tienda (todos los avisos, rechazados)."""
        if not connection.webhook_secret:
            from ...models import StoreConnection

            with transaction.atomic():
                fresh = StoreConnection.objects.select_for_update().get(pk=connection.pk)
                if not fresh.webhook_secret:
                    fresh.webhook_secret = secrets.token_urlsafe(32)
                    fresh.save(update_fields=["webhook_secret_encrypted", "updated_at"])
            connection.webhook_secret_encrypted = fresh.webhook_secret_encrypted
        return connection.webhook_secret

    def register_webhooks(self, connection, url, events):
        """Crea los webhooks que falten y vuelve a activar los que WooCommerce
        desactivó por fallas (o un comerciante pausó). Idempotente: es lo que
        corre también en cada repaso (``supports_reconciliation``)."""
        secret = self.ensure_webhook_secret(connection)
        delivery_url = f"{url}?{urlencode({'store': connection.pk})}"
        existing = _json(self.api_request(connection, "GET", "webhooks", params={"per_page": 100}))
        mine = {}
        for hook in existing if isinstance(existing, list) else []:
            if isinstance(hook, dict) and _text(hook.get("delivery_url")) == delivery_url:
                mine.setdefault(_text(hook.get("topic")), hook)

        changed = []
        for topic in events:
            hook = mine.get(topic)
            if hook is None:
                self.api_request(
                    connection,
                    "POST",
                    "webhooks",
                    json_body={
                        "name": f"Rótulos: {topic}",
                        "topic": topic,
                        "delivery_url": delivery_url,
                        "secret": secret,
                        "status": "active",
                    },
                )
                changed.append(topic)
            elif _text(hook.get("status")) != "active":
                # El secreto se reescribe por las dudas: uno creado a mano o
                # con otro secreto nunca validaría la firma.
                self.api_request(
                    connection, "PUT", f"webhooks/{hook['id']}", json_body={"status": "active", "secret": secret}
                )
                changed.append(topic)
        return changed

    def configure_admin_print(self, connection, base_url):
        """Escribe en nuestro plugin de WordPress ("Rótulos de envío") a dónde
        pedir la impresión y la cotización del envío, qué tienda es y el
        secreto con el que firmar, por
        la API de ajustes de WooCommerce (el plugin declara el grupo
        ``rotulos``). Sin el plugin instalado ese grupo no existe (404): no es
        un error, la tienda imprime desde la app."""
        settings_values = {
            "rotulos_print_link_url": f"{base_url}{reverse('woocommerce-print-link')}",
            "rotulos_rates_url": f"{base_url}{reverse('woocommerce-rates')}",
            "rotulos_store_id": str(connection.pk),
            "rotulos_secret": self.ensure_webhook_secret(connection),
        }
        try:
            response = self.api_request(
                connection,
                "POST",
                "settings/rotulos/batch",
                json_body={"update": [{"id": key, "value": value} for key, value in settings_values.items()]},
            )
        except ProviderNotFoundError:
            _remember_print_plugin(connection, False)
            return False
        except ProviderError as exc:
            # Una falla pasajera no dice nada del plugin: se deja lo que había.
            logger.warning("No se pudo configurar el plugin de impresión de la tienda %s: %s", connection.pk, exc)
            return False
        data = _json(response)
        items = data.get("update") if isinstance(data, dict) else None
        failed = [item for item in items or [] if isinstance(item, dict) and item.get("error")]
        if failed:
            logger.warning("El plugin de impresión de la tienda %s rechazó ajustes: %s", connection.pk, failed)
        _remember_print_plugin(connection, not failed)
        return not failed

    def verify_webhook(self, raw_body, headers, connection=None):
        secret = connection.webhook_secret if connection is not None else ""
        signature = get_header(headers, WEBHOOK_SIGNATURE_HEADER).strip()
        if not secret or not signature:
            return False
        digest = hmac.new(secret.encode("utf-8"), raw_body or b"", hashlib.sha256).digest()
        return hmac.compare_digest(signature.encode("utf-8"), base64.b64encode(digest))

    def parse_webhook(self, raw_body, headers):
        topic = get_header(headers, WEBHOOK_TOPIC_HEADER).strip()
        if not topic:
            # El ping que WooCommerce manda al crear el webhook: un form con
            # webhook_id, sin topic. Se contesta 200 y no se encola nada.
            if "webhook_id" in parse_qs((raw_body or b"").decode("utf-8", "replace")):
                return None
            raise ValueError("Falta el header X-WC-Webhook-Topic.")
        source = get_header(headers, WEBHOOK_SOURCE_HEADER).strip()
        try:
            payload = json.loads((raw_body or b"").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("El payload debe ser JSON válido.") from None
        if not isinstance(payload, dict):
            raise ValueError("El payload debe ser un objeto.")
        # Como en Shopify: el worker vuelve a pedir el pedido, así que en la
        # cola solo queda el id (nada del comprador guardado de más).
        payload = {"id": payload.get("id"), "date_modified_gmt": payload.get("date_modified_gmt")}
        return WebhookMessage(
            store_id=external_store_id_for(source) if source else "",
            event_type=topic,
            resource_id=_text(payload.get("id") or ""),
            payload=payload,
        )
