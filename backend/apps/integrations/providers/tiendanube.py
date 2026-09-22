"""Proveedor Tiendanube (https://tiendanube.github.io/api-documentation/).

Implementado: instalación OAuth (URL de autorización y canje del código),
cliente de la API (``api_request``) y datos de la tienda, verificación de
la firma de un webhook, traducción de un pedido a ``NormalizedOrder``,
devolución del estado de despacho y tracking (``push_fulfillment``) y los
rótulos que pide la tienda (``normalize_label_request``,
``push_label_status``, ``register_shipping_carrier`` — ver ``store_labels``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.utils.dateparse import parse_datetime

from .base import (
    NormalizedLabelRequest,
    NormalizedOrder,
    OAuthResult,
    ProviderAuthError,
    ProviderError,
    ProviderNotFoundError,
    ProviderRejectedError,
    StoreInfo,
    StoreProvider,
    get_header,
)

logger = logging.getLogger(__name__)

AUTHORIZE_URL = "https://www.tiendanube.com/apps/{app_id}/authorize"
TOKEN_URL = "https://www.tiendanube.com/apps/authorize/token"
API_BASE_URL = "https://api.tiendanube.com/{version}/{store_id}"

# Header con el HMAC-SHA256 del body, firmado con el client secret de la app.
SIGNATURE_HEADER = "X-Linkedstore-Hmac-Sha256"

COUNTRY_NAMES = {"AR": "Argentina"}

# Orden de avance de un fulfillment order (la API permite saltar hacia
# adelante, nunca volver): sirve para no "retroceder" uno ya más avanzado.
FULFILLMENT_STATUS_SEQUENCE = (
    "UNPACKED",
    "IN_PREPARATION",
    "PACKED",
    "DISPATCHED",
    "READY_FOR_PICKUP",
    "DELIVERED",
)


def _fulfillment_rank(status):
    try:
        return FULFILLMENT_STATUS_SEQUENCE.index(status)
    except ValueError:
        return -1


def _text(value):
    return str(value).strip() if value is not None else ""


def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _timeout():
    return getattr(settings, "TIENDANUBE_HTTP_TIMEOUT_SECONDS", 10)


def _user_agent():
    # Tiendanube responde 400 a cualquier request sin User-Agent.
    return getattr(settings, "TIENDANUBE_USER_AGENT", "") or "ROTULOS_PERSO"


def _json_object(response):
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, (dict, list)) else {}


def _local_status(raw):
    """Estado local según la tienda: pedido cancelado, o envío despachado
    (shipping_status "shipped", que la API documenta como "fulfilled") o
    entregado. Cualquier otro valor no cambia el estado local."""
    if _text(raw.get("status")).lower() == "cancelled":
        return "cancelled"
    shipping_status = _text(raw.get("shipping_status")).lower()
    if shipping_status == "delivered":
        return "delivered"
    if shipping_status == "shipped":
        return "dispatched"
    return ""


def _shipping_option(value):
    # Según la versión de la API llega como texto o como objeto con "name".
    if isinstance(value, dict):
        return _text(value.get("name"))
    return _text(value)


class TiendanubeProvider(StoreProvider):
    platform = "tiendanube"

    def verify_webhook(self, raw_body, headers):
        secret = getattr(settings, "TIENDANUBE_CLIENT_SECRET", "")
        signature = get_header(headers, SIGNATURE_HEADER).strip()
        if not secret or not signature:
            return False
        digest = hmac.new(secret.encode("utf-8"), raw_body or b"", hashlib.sha256).digest()
        # La documentación no aclara la codificación del HMAC: se acepta hex
        # o base64 hasta confirmarlo contra una tienda real (fase 2).
        candidates = (("hex", digest.hex()), ("base64", base64.b64encode(digest).decode("ascii")))
        received = signature.encode("utf-8")
        for encoding, candidate in candidates:
            if hmac.compare_digest(received, candidate.encode("ascii")):
                # Temporal: registra qué codificación usa Tiendanube (nunca la firma).
                logger.warning("Firma de webhook de Tiendanube válida, codificación: %s", encoding)
                return True
        return False

    def normalize_order(self, raw):
        if not isinstance(raw, dict) or raw.get("id") in (None, ""):
            raise ValueError("El pedido de Tiendanube no trae 'id'.")

        shipping = raw.get("shipping_address") or {}
        customer = raw.get("customer") or {}

        items = []
        total_weight = Decimal("0")
        has_weight = False
        for product in raw.get("products") or []:
            if not isinstance(product, dict):
                continue
            quantity = _to_int(product.get("quantity"), default=1)
            weight = _to_decimal(product.get("weight"))
            if weight is not None:
                total_weight += weight * quantity
                has_weight = True
            items.append(
                {
                    "name": _text(product.get("name")),
                    "sku": _text(product.get("sku")),
                    "quantity": quantity,
                    "weight_kg": str(weight) if weight is not None else None,
                }
            )
        if not has_weight:
            order_weight = _to_decimal(raw.get("weight"))
            if order_weight is not None:
                total_weight, has_weight = order_weight, True

        floor = _text(shipping.get("floor"))
        locality = _text(shipping.get("locality"))
        reference_parts = []
        if floor:
            reference_parts.append(f"Piso/Depto: {floor}")
        if locality:
            reference_parts.append(f"Barrio: {locality}")

        country_code = _text(shipping.get("country"))

        return NormalizedOrder(
            external_id=_text(raw.get("id")),
            external_number=_text(raw.get("number")),
            recipient_name=_text(shipping.get("name") or raw.get("contact_name") or customer.get("name")),
            street=_text(shipping.get("address")),
            number=_text(shipping.get("number")),
            city=_text(shipping.get("city")),
            state=_text(shipping.get("province")),
            postal_code=_text(shipping.get("zipcode")),
            country=COUNTRY_NAMES.get(country_code.upper(), country_code) or "Argentina",
            reference=" · ".join(reference_parts),
            contact_email=_text(raw.get("contact_email") or customer.get("email")),
            contact_phone=_text(raw.get("contact_phone") or shipping.get("phone")),
            description=", ".join(f"{item['quantity']}x {item['name']}" for item in items if item["name"]),
            shipping_option=_shipping_option(raw.get("shipping_option")),
            status=_local_status(raw),
            total_weight_kg=total_weight if has_weight else None,
            items=items,
            external_updated_at=parse_datetime(_text(raw.get("updated_at"))) if raw.get("updated_at") else None,
            raw=raw,
        )

    # --- OAuth -----------------------------------------------------------

    def build_authorize_url(self, state):
        app_id = getattr(settings, "TIENDANUBE_APP_ID", "")
        if not app_id:
            raise ProviderError("TIENDANUBE_APP_ID no está configurado.")
        return f"{AUTHORIZE_URL.format(app_id=app_id)}?{urlencode({'state': state})}"

    def exchange_code(self, code):
        app_id = getattr(settings, "TIENDANUBE_APP_ID", "")
        secret = getattr(settings, "TIENDANUBE_CLIENT_SECRET", "")
        if not app_id or not secret:
            raise ProviderError("Faltan TIENDANUBE_APP_ID o TIENDANUBE_CLIENT_SECRET.")
        try:
            response = requests.post(
                TOKEN_URL,
                json={
                    "client_id": app_id,
                    "client_secret": secret,
                    "grant_type": "authorization_code",
                    "code": code,
                },
                headers={"User-Agent": _user_agent()},
                timeout=_timeout(),
            )
        except requests.RequestException as exc:
            raise ProviderError(f"No se pudo contactar a Tiendanube: {exc.__class__.__name__}.") from exc

        if response.status_code >= 500:
            raise ProviderError(f"Tiendanube respondió HTTP {response.status_code} al canjear el código.")
        data = _json_object(response)
        if not isinstance(data, dict):
            data = {}
        # Un código vencido (duran 5 minutos) o ya usado vuelve con "error",
        # a veces incluso con HTTP 200.
        if response.status_code >= 400 or data.get("error") or not data.get("access_token"):
            detail = data.get("error_description") or data.get("error") or f"HTTP {response.status_code}"
            raise ProviderAuthError(f"Tiendanube rechazó el código de autorización: {detail}")

        store_id = data.get("user_id") or data.get("store_id")
        if not store_id:
            raise ProviderError("La respuesta de Tiendanube no trae el id de la tienda.")
        return OAuthResult(
            access_token=str(data["access_token"]),
            external_store_id=str(store_id),
            scopes=_text(data.get("scope")),
        )

    # --- API -------------------------------------------------------------

    def api_request(self, connection, method, path, **kwargs):
        """Llamada autenticada a la API de la tienda de ``connection``.
        Devuelve el JSON (dict o list). ``ProviderAuthError`` con 401/403,
        ``ProviderError`` con cualquier otro error (429 incluido)."""
        token = connection.access_token
        if not token:
            raise ProviderAuthError("La tienda no tiene un token guardado.")
        version = getattr(settings, "TIENDANUBE_API_VERSION", "2025-03")
        url = f"{API_BASE_URL.format(version=version, store_id=connection.external_store_id)}/{path.lstrip('/')}"
        headers = {
            # Confirmado contra una tienda real (18/09/2026): Tiendanube
            # acepta indistintamente "Authorization: Bearer" y el legacy
            # "Authentication: bearer". Mandamos solo el que pide la
            # documentación actual.
            "Authorization": f"Bearer {token}",
            "User-Agent": _user_agent(),
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            response = requests.request(method, url, headers=headers, timeout=_timeout(), **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"No se pudo contactar a Tiendanube: {exc.__class__.__name__}.") from exc

        if response.status_code in (401, 403):
            raise ProviderAuthError(f"Tiendanube rechazó el token de la tienda (HTTP {response.status_code}).")
        if response.status_code == 404:
            raise ProviderNotFoundError(f"Tiendanube: no existe {method} /{path.lstrip('/')} (HTTP 404).")
        if response.status_code in (400, 422):
            data = _json_object(response)
            detail = data.get("description") or data.get("message") if isinstance(data, dict) else ""
            raise ProviderRejectedError(
                f"Tiendanube rechazó {method} /{path.lstrip('/')} (HTTP {response.status_code})"
                + (f": {str(detail)[:300]}" if detail else ".")
            )
        if response.status_code == 429:
            raise ProviderError("Tiendanube: se alcanzó el límite de pedidos a la API (HTTP 429).")
        if response.status_code >= 400:
            raise ProviderError(f"Tiendanube respondió HTTP {response.status_code} en {method} /{path.lstrip('/')}.")
        return _json_object(response)

    def get_store_info(self, connection):
        data = self.api_request(connection, "GET", "store")
        if not isinstance(data, dict):
            data = {}

        # "name" viene por idioma: {"es": "...", "pt": "...", "en": "..."}.
        name = data.get("name")
        if isinstance(name, dict):
            language = _text(data.get("main_language")) or "es"
            name = name.get(language) or name.get("es") or next((value for value in name.values() if value), "")

        domains = data.get("domains") or []
        domain = _text(domains[0] if domains else "") or _text(data.get("original_domain"))
        store_url = domain if not domain or domain.startswith("http") else f"https://{domain}"

        features = data.get("features")
        return StoreInfo(
            name=_text(name),
            store_url=store_url,
            email=_text(data.get("email")),
            features=[_text(feature) for feature in features] if isinstance(features, list) else [],
        )

    def get_order(self, connection, order_id):
        data = self.api_request(connection, "GET", f"orders/{order_id}")
        if not isinstance(data, dict) or not data:
            raise ProviderError("Tiendanube devolvió el pedido vacío o con un formato inesperado.")
        return data

    def list_orders(self, connection, *, created_at_min="", page=1, per_page=200):
        params = {"page": page, "per_page": per_page}
        if created_at_min:
            params["created_at_min"] = created_at_min
        try:
            data = self.api_request(connection, "GET", "orders", params=params)
        except ProviderNotFoundError:
            # Sin resultados o pasada la última página, Tiendanube responde
            # 404 ("Last page is N") en vez de una lista vacía.
            return []
        if not isinstance(data, list):
            raise ProviderError("Tiendanube devolvió la lista de pedidos con un formato inesperado.")
        return data

    def push_fulfillment(self, connection, order_id, *, status, tracking_code="", tracking_url="", notify_customer=True):
        """Actualiza cada fulfillment order del pedido: estado (solo hacia
        adelante) y tracking (solo si cambió), en un único PATCH por
        fulfillment order. Sin ``tracking_code`` solo cambia el estado."""
        fulfillment_orders = self.api_request(connection, "GET", f"orders/{order_id}/fulfillment-orders")
        if not isinstance(fulfillment_orders, list):
            raise ProviderError("Tiendanube devolvió los fulfillment orders con un formato inesperado.")

        tracking_info = None
        if tracking_code:
            tracking_info = {"code": tracking_code, "notify_customer": notify_customer}
            if tracking_url:
                tracking_info["url"] = tracking_url

        updated = []
        for fulfillment in fulfillment_orders:
            if not isinstance(fulfillment, dict) or not fulfillment.get("id"):
                continue
            body = {}
            current_status = _text(fulfillment.get("status")).upper()
            if _fulfillment_rank(current_status) < _fulfillment_rank(status):
                body["status"] = status
            current_tracking = fulfillment.get("tracking_info")
            current_code = _text(current_tracking.get("code")) if isinstance(current_tracking, dict) else ""
            if tracking_info and current_code != tracking_code:
                body["tracking_info"] = tracking_info
            if not body:
                continue
            self.api_request(
                connection, "PATCH", f"orders/{order_id}/fulfillment-orders/{fulfillment['id']}", json=body
            )
            updated.append(_text(fulfillment["id"]))
        return updated

    # --- Rótulos pedidos por la tienda (Labels API) ----------------------

    def normalize_label_request(self, raw):
        if not isinstance(raw, dict) or raw.get("id") in (None, ""):
            raise ValueError("El pedido de rótulo de Tiendanube no trae 'id'.")

        info = raw.get("fulfillment_order_info") or {}
        if not isinstance(info, dict):
            info = {}
        recipient = info.get("recipient") or {}
        destination = info.get("destination") or {}
        shipping = info.get("shipping") or {}
        tracking = info.get("tracking_info") or {}

        # Igual criterio que normalize_order: piso y barrio no son parte del
        # domicilio, van como referencia para el repartidor.
        reference_parts = []
        floor = _text(destination.get("floor"))
        locality = _text(destination.get("locality"))
        if floor:
            reference_parts.append(f"Piso/Depto: {floor}")
        if locality:
            reference_parts.append(f"Barrio: {locality}")
        for extra in (destination.get("between_streets"), destination.get("reference")):
            if _text(extra):
                reference_parts.append(_text(extra))

        country = destination.get("country") or {}
        country_name = _text(country.get("name")) if isinstance(country, dict) else _text(country)
        country_code = _text(country.get("code")) if isinstance(country, dict) else ""
        province = destination.get("province") or {}

        option = shipping.get("option") or {}
        created_at = _text(info.get("created_at"))

        return NormalizedLabelRequest(
            external_label_id=_text(raw.get("id")),
            external_fulfillment_order_id=_text(info.get("id")),
            recipient_name=_text(recipient.get("name")),
            street=_text(destination.get("street")),
            number=_text(destination.get("number")),
            city=_text(destination.get("city")),
            state=_text(province.get("name")) if isinstance(province, dict) else _text(province),
            postal_code=_text(destination.get("zipcode")),
            country=country_name or COUNTRY_NAMES.get(country_code.upper(), country_code) or "Argentina",
            reference=" · ".join(reference_parts),
            # El callback identifica el ENVÍO, no el pedido: si no viene el
            # número del pedido se usa el del fulfillment order, que es lo
            # que el comerciante ve al lado del envío en su admin.
            order_number=_text(info.get("order_number") or info.get("number")),
            shipping_option=_shipping_option(option.get("name") if isinstance(option, dict) else option),
            tracking_code=_text(tracking.get("code") or tracking.get("number")),
            tracking_url=_text(tracking.get("url")),
            created_at=parse_datetime(created_at) if created_at else None,
        )

    def register_shipping_carrier(self, connection, *, name, rates_url, labels_url, types="ship"):
        """Da de alta (o actualiza) NUESTRO medio de envío en la tienda.

        Idempotente: si ya existe uno con este nombre se actualizan sus
        callbacks en vez de crear otro — una tienda que reinstala la app no
        puede terminar con dos medios de envío iguales en su checkout.

        Ojo: ``rates_url`` es OBLIGATORIO para Tiendanube y es el endpoint
        que cotiza precios en el checkout. Mientras no exista, dar de alta
        el carrier deja a la tienda con un medio de envío que no contesta
        (ver ``store_labels.register_carrier``, que por eso no corre solo).
        """
        existing = self.api_request(connection, "GET", "shipping_carriers")
        current = None
        if isinstance(existing, list):
            current = next(
                (
                    carrier
                    for carrier in existing
                    if isinstance(carrier, dict) and _text(carrier.get("name")) == name
                ),
                None,
            )

        body = {
            "name": name,
            "callback_url": rates_url,
            "callback_labels_url": labels_url,
            "types": types,
        }
        if current is not None:
            return self.api_request(
                connection, "PUT", f"shipping_carriers/{current['id']}", json=body
            )
        return self.api_request(connection, "POST", "shipping_carriers", json=body)

    def push_label_status(self, connection, fulfillment_order_id, label_id, *, status, documents=None, reason=None):
        body = {"status": status}
        if documents:
            body["documents"] = documents
        if reason:
            body["reason"] = reason
        return self.api_request(
            connection,
            "PATCH",
            f"fulfillment-orders/{fulfillment_order_id}/labels/{label_id}",
            json=body,
        )

    def register_webhooks(self, connection, url, events):
        existing = self.api_request(connection, "GET", "webhooks", params={"per_page": 200})
        registered = set()
        if isinstance(existing, list):
            registered = {
                (_text(item.get("event")), _text(item.get("url"))) for item in existing if isinstance(item, dict)
            }
        created = []
        for event in events:
            if (event, url) in registered:
                continue
            self.api_request(connection, "POST", "webhooks", json={"event": event, "url": url})
            created.append(event)
        return created
