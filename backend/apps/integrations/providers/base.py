"""Contrato común de las plataformas de tienda online.

Cada plataforma (Tiendanube, Shopify; WooCommerce, Mercado Libre... a
futuro) implementa ``StoreProvider`` y traduce SUS pedidos a un
``NormalizedOrder``. Todo lo que viene después (crear/actualizar el pedido,
generar el rótulo) trabaja sobre ``NormalizedOrder`` y no sabe de qué
plataforma vino: sumar una plataforma es escribir un proveedor, no tocar
``apps.orders``.

Lo que cambia entre plataformas y el resto del código NO debe saber vive
acá como atributo o método del proveedor: qué eventos de webhook registrar
y cuáles traen un pedido (``order_sync_events``), cómo se lee un webhook
(``parse_webhook``), cómo se valida la vuelta del OAuth
(``verify_callback``), qué estado de despacho le corresponde a un estado
local (``fulfillment_status_for``) y si el token vence y se renueva
(``refresh_access_token``).
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
    # Solo en plataformas con tokens que vencen (Shopify: el access token
    # dura una hora y se renueva con el refresh token, que dura 90 días).
    # Vacío/None = token sin vencimiento, como el de Tiendanube.
    refresh_token: str = ""
    expires_in: int | None = None
    refresh_token_expires_in: int | None = None


@dataclass
class WebhookMessage:
    """Un webhook ya leído, sin importar dónde puso cada plataforma cada
    dato (Tiendanube: todo en el body; Shopify: tienda y evento en headers)."""

    store_id: str
    event_type: str
    resource_id: str = ""
    payload: dict = field(default_factory=dict)


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

    # Avisos de la plataforma que traen un pedido nuevo o cambiado (los
    # procesa ``handlers.sync_order``) y los que significan "desinstalaron
    # la app" (``handlers.app_uninstalled``).
    order_sync_events = ()
    uninstall_events = ()
    # Otros avisos que también hay que registrar en la tienda.
    extra_webhook_events = ()
    # Webhooks de privacidad -> qué hacer (``"store"``, ``"customer"`` o
    # ``"data_request"``, ver ``handlers``). Cada plataforma los llama
    # distinto (``store/redact`` en Tiendanube, ``shop/redact`` en Shopify).
    privacy_events = {}
    # False mientras la plataforma no sepa traer pedidos (``list_orders``):
    # la conexión igual se completa, solo no se lanza la importación.
    supports_order_import = True
    # La URL de autorización depende de la tienda (Shopify: cada tienda
    # tiene su dominio), así que hay que pedírsela al comerciante.
    requires_shop_domain = False
    # El callback sin ``state`` se trata como un intento inválido. En
    # Tiendanube puede faltar (instalación desde su tienda de apps); en
    # Shopify toda instalación pasa antes por nosotros y siempre lo trae.
    requires_oauth_state = False

    @property
    def webhook_events(self):
        return tuple(self.order_sync_events) + tuple(self.uninstall_events) + tuple(self.extra_webhook_events)

    @property
    def orders_page_size(self):
        return 200

    def build_authorize_url(self, state, *, shop_domain=""):
        """URL a la que se manda al comerciante para instalar/autorizar la
        app; ``state`` vuelve tal cual en el callback. ``shop_domain`` solo
        en plataformas con ``requires_shop_domain``."""
        raise NotImplementedError

    def normalize_shop_domain(self, value):
        """Dominio de la tienda validado, o ``ValueError`` si no sirve.
        Solo lo usan las plataformas con ``requires_shop_domain``."""
        return ""

    def verify_callback(self, query_params):
        """``True`` si la vuelta del OAuth viene firmada por la plataforma.
        Tiendanube no firma su callback (lo protege el ``state``)."""
        return True

    def callback_shop_domain(self, query_params):
        """Dominio de la tienda que trae el callback (``""`` si la plataforma
        no lo manda). ``ValueError`` si viene y no es válido."""
        return ""

    def exchange_code(self, code, *, shop_domain=""):
        """Canjea el código del callback OAuth -> ``OAuthResult``.
        ``ProviderAuthError`` si la plataforma lo rechaza."""
        raise NotImplementedError

    def refresh_access_token(self, connection):
        """Renueva un token que vence -> ``OAuthResult`` con el token nuevo
        (y el refresh token nuevo: rotan). ``ProviderAuthError`` si el
        refresh token ya no sirve y hay que reinstalar. Solo lo implementan
        las plataformas cuyos tokens vencen (ver ``apps.integrations.tokens``)."""
        raise NotImplementedError

    def parse_webhook(self, raw_body, headers):
        """Webhook ya verificado -> ``WebhookMessage``. ``ValueError`` (con
        el mensaje para responder 400) si no se puede leer."""
        raise NotImplementedError

    def fulfillment_status_for(self, order_status):
        """Estado de despacho de la plataforma que corresponde a un estado
        local del pedido (``Order.Status``), o ``None`` si ese estado no se
        informa a la tienda."""
        return None

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
