"""Contrato común de las plataformas de tienda online.

Cada plataforma (Tiendanube hoy; Shopify, Mercado Libre, WooCommerce... a
futuro) implementa ``StoreProvider`` y traduce SUS pedidos a un
``NormalizedOrder``. Todo lo que viene después (crear/actualizar el pedido,
generar el rótulo) trabaja sobre ``NormalizedOrder`` y no sabe de qué
plataforma vino: sumar una plataforma es escribir un proveedor, no tocar
``apps.orders``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass
class NormalizedOrder:
    """Un pedido de tienda ya traducido a los campos propios (ver
    ``apps.orders.ingestion.upsert_store_order``)."""

    external_id: str
    external_number: str = ""
    recipient_name: str = ""
    street: str = ""
    number: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = "Argentina"
    reference: str = ""
    contact_email: str = ""
    contact_phone: str = ""
    description: str = ""
    shipping_option: str = ""
    # Estado local que corresponde según la tienda ("cancelled",
    # "dispatched", "delivered") o "" si la tienda no dice nada del envío.
    status: str = ""
    package_count: int = 1
    total_weight_kg: Decimal | None = None
    items: list = field(default_factory=list)
    external_updated_at: datetime | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class NormalizedLabelRequest:
    """Un rótulo que la TIENDA nos pidió generar, ya traducido a los campos
    que necesita el render (ver ``apps.integrations.store_labels``).

    Es el equivalente de ``NormalizedOrder`` para el otro sentido: ahí la
    plataforma nos avisa de un pedido y lo guardamos; acá nos pide un
    rótulo y lo dibujamos sin guardar pedido alguno. Trae todo lo que el
    rótulo necesita, así que no hace falta volver a consultar la API.
    """

    external_label_id: str
    external_fulfillment_order_id: str = ""
    recipient_name: str = ""
    street: str = ""
    number: str = ""
    city: str = ""
    state: str = ""
    postal_code: str = ""
    country: str = "Argentina"
    reference: str = ""
    order_number: str = ""
    shipping_option: str = ""
    tracking_code: str = ""
    tracking_url: str = ""
    created_at: datetime | None = None


class ProviderError(Exception):
    """Falla hablando con la plataforma (red, 5xx, límite de uso, respuesta
    inesperada): en general se puede reintentar."""


class ProviderAuthError(ProviderError):
    """La plataforma rechazó las credenciales (código OAuth vencido o ya
    usado, token revocado): reintentar no lo arregla."""


class ProviderNotFoundError(ProviderError):
    """El recurso pedido no existe en la plataforma (HTTP 404)."""


class ProviderRejectedError(ProviderError):
    """La plataforma rechazó el pedido por inválido (HTTP 400/422, p. ej.
    una transición de estado no permitida): reintentar no lo arregla."""


@dataclass
class OAuthResult:
    access_token: str
    external_store_id: str
    scopes: str = ""


@dataclass
class StoreInfo:
    name: str = ""
    store_url: str = ""
    email: str = ""
    # Funcionalidades habilitadas en el plan de esa tienda. La API de
    # rótulos, por ejemplo, solo existe para algunos planes: saberlo al
    # conectar evita descubrirlo con un 403 cuando el comerciante ya
    # apretó "imprimir etiquetas".
    features: list = field(default_factory=list)


class StoreProvider:
    """Lo que cada plataforma tiene que saber hacer. Las operaciones que
    faltan (traer pedidos, devolver el tracking) se agregan acá a medida que
    se implementan."""

    platform = ""

    def build_authorize_url(self, state):
        """URL a la que se manda al comerciante para instalar/autorizar la
        app; ``state`` vuelve tal cual en el callback."""
        raise NotImplementedError

    def exchange_code(self, code):
        """Canjea el código del callback OAuth -> ``OAuthResult``.
        ``ProviderAuthError`` si la plataforma lo rechaza."""
        raise NotImplementedError

    def get_store_info(self, connection):
        """Datos visibles de la tienda (nombre, URL) -> ``StoreInfo``."""
        raise NotImplementedError

    def get_order(self, connection, order_id):
        """Pedido crudo completo por id (dict)."""
        raise NotImplementedError

    def list_orders(self, connection, *, created_at_min="", page=1, per_page=200):
        """Una página de pedidos crudos (list); vacía pasada la última."""
        raise NotImplementedError

    def push_fulfillment(self, connection, order_id, *, status, tracking_code="", tracking_url="", notify_customer=True):
        """Informa a la tienda que el pedido se despachó/entregó (``status``
        en el vocabulario de la plataforma) con su tracking. Idempotente: no
        repite lo que la tienda ya tiene. Devuelve lo que actualizó."""
        raise NotImplementedError

    def register_webhooks(self, connection, url, events):
        """Registra en la tienda los ``events`` que todavía no apunten a
        ``url``. Idempotente. Devuelve los eventos registrados ahora."""
        raise NotImplementedError

    def verify_webhook(self, raw_body, headers):
        """``True`` si el webhook viene firmado por la plataforma.
        ``raw_body``: bytes tal cual llegaron; ``headers``: mapping de headers."""
        raise NotImplementedError

    def normalize_order(self, raw):
        """Pedido crudo de la plataforma -> ``NormalizedOrder``. ``ValueError``
        si el pedido no se puede identificar (sin id)."""
        raise NotImplementedError

    def normalize_label_request(self, raw):
        """Un elemento del callback de rótulos -> ``NormalizedLabelRequest``.
        ``ValueError`` si no se puede identificar (sin id de etiqueta)."""
        raise NotImplementedError

    def register_shipping_carrier(self, connection, *, name, rates_url, labels_url, types="ship"):
        """Da de alta o actualiza nuestro medio de envío en la tienda, con
        los callbacks de cotización y de rótulos. Idempotente."""
        raise NotImplementedError

    def push_label_status(self, connection, fulfillment_order_id, label_id, *, status, documents=None, reason=None):
        """Informa a la plataforma en qué quedó un rótulo que nos pidió:
        listo para descargar (con ``documents``) o fallido (con ``reason``).
        Idempotente del lado nuestro; la plataforma rechaza un cambio de
        estado inválido con ``ProviderRejectedError``."""
        raise NotImplementedError


def get_header(headers, name):
    """Header por nombre sin importar mayúsculas (``request.headers`` ya lo
    resuelve, pero un dict común de tests no)."""
    wanted = name.lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == wanted:
            return str(value or "")
    return ""
