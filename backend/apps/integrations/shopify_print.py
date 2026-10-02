"""Rótulos impresos desde el admin de Shopify (extensión de impresión).

El comerciante tilda pedidos en SU lista de pedidos de Shopify, abre el menú
"Imprimir" y elige nuestros rótulos. Esa opción es una *admin print action
extension* (el proyecto de la extensión vive aparte, ver ``rotulos-extension/``):
Shopify le pasa los pedidos elegidos y ella necesita una URL con el documento
a mostrar en la vista previa de impresión.

Dos pasos, a propósito:

1. ``POST /shopify/print-link/`` — la extensión lo llama con ``fetch``, y
   Shopify le agrega solo un *ID token* (JWT HS256 firmado con el client
   secret de la app; ``verify_id_token``). Ese token dice QUÉ tienda es; con
   él se valida la tienda, se resuelven los pedidos y se devuelve un enlace.
2. ``GET /shopify/print/<token>`` — el enlace: lo carga la vista previa de
   impresión de Shopify, que es un documento del navegador y no un fetch,
   así que ahí no viaja el ID token. Lo autentica el propio enlace, firmado
   por nosotros y de vida corta (``make_print_token``), igual que la descarga
   de los rótulos de Tiendanube.

Separarlo así evita depender de cómo la vista previa carga ``src`` (la
documentación no dice si le agrega credenciales) y de que el ID token, que
dura un minuto, siga vivo cuando el comerciante aprieta "Imprimir".

Un pedido elegido que todavía no tenemos (más viejo que la importación
inicial, o un aviso que se perdió) se trae en el momento: el comerciante eligió
imprimirlo, no puede faltar del PDF.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import jwt
from django.conf import settings
from django.core import signing
from django.urls import reverse

from apps.labels.batch_views import _render_combined_pdf
from apps.orders.ingestion import upsert_store_order
from apps.orders.models import Order

from .models import StoreConnection
from .providers import get_provider
from .providers.base import ProviderError, ProviderNotFoundError
from .store_labels import resolve_template

logger = logging.getLogger(__name__)

PRINT_TOKEN_SALT = "apps.integrations.shopify-print"
# Lo que Shopify deja seleccionar de una vez en la lista de pedidos ronda los
# 250; más que eso es un pedido raro o malicioso, y cada uno es una página.
MAX_ORDERS_PER_PRINT = 250


class PrintError(Exception):
    """No se puede armar la impresión; el mensaje es para el comerciante."""


def verify_id_token(token):
    """Dominio de la tienda del ID token de Shopify, o ``PrintError``.

    Chequea lo que pide Shopify: firma HS256 con el client secret, ``aud`` =
    client ID, vigencia (``exp``/``nbf``, con unos segundos de tolerancia
    por relojes desparejos) y que ``iss`` y ``dest`` sean la misma tienda."""
    client_id = getattr(settings, "SHOPIFY_CLIENT_ID", "")
    secret = getattr(settings, "SHOPIFY_CLIENT_SECRET", "")
    if not client_id or not secret:
        raise PrintError("La app de Shopify no está configurada en el servidor.")
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=client_id,
            leeway=10,
            options={"require": ["exp", "nbf", "iss", "dest", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise PrintError("La sesión de Shopify no es válida o venció. Volvé a abrir la impresión.") from exc

    dest_host = urlparse(str(claims.get("dest") or "")).hostname or ""
    iss_host = urlparse(str(claims.get("iss") or "")).hostname or ""
    if not dest_host or dest_host != iss_host:
        raise PrintError("La sesión de Shopify no corresponde a una tienda.")
    try:
        return get_provider("shopify").normalize_shop_domain(dest_host)
    except ValueError as exc:
        raise PrintError("La sesión de Shopify no corresponde a una tienda.") from exc


def connection_for_shop(shop_domain):
    connection = StoreConnection.objects.filter(
        platform=StoreConnection.Platform.SHOPIFY,
        external_store_id=shop_domain,
        status=StoreConnection.Status.ACTIVE,
    ).first()
    if connection is None:
        raise PrintError("Esta tienda no está conectada a la app. Abrí la app desde Shopify para conectarla.")
    if connection.owner_id is None:
        raise PrintError("Esta tienda todavía no está vinculada a una cuenta. Abrí la app desde Shopify para vincularla.")
    return connection


def legacy_order_ids(selected):
    """``["gid://shopify/Order/123", ...]`` -> ``["123", ...]``, sin repetir
    y en el orden elegido (es el orden de las páginas del PDF)."""
    if not isinstance(selected, list) or not selected:
        raise PrintError("No llegó ningún pedido para imprimir.")
    if len(selected) > MAX_ORDERS_PER_PRINT:
        raise PrintError(f"Se pueden imprimir hasta {MAX_ORDERS_PER_PRINT} pedidos por vez.")
    ids = []
    for value in selected:
        legacy_id = str(value or "").strip().rsplit("/", 1)[-1]
        if not legacy_id.isdigit():
            raise PrintError("Uno de los pedidos elegidos no es válido.")
        if legacy_id not in ids:
            ids.append(legacy_id)
    return ids


def resolve_orders(connection, legacy_ids):
    """Pedidos nuestros para esos ids de Shopify, en el mismo orden. Los que
    falten se piden a Shopify y se guardan (``upsert_store_order``, el mismo
    camino que un aviso). Devuelve ``(orders, missing_ids)``: ``missing`` son
    los que Shopify tampoco tiene (borrados) — se imprimen los demás."""
    existing = {
        order.external_id: order
        for order in Order.objects.filter(store_connection=connection, external_id__in=legacy_ids).select_related(
            "address", "store_connection"
        )
    }
    provider = get_provider(connection.platform)
    orders, missing = [], []
    for legacy_id in legacy_ids:
        order = existing.get(legacy_id)
        if order is None:
            try:
                order, _ = upsert_store_order(connection, provider.normalize_order(provider.get_order(connection, legacy_id)))
            except ProviderNotFoundError:
                missing.append(legacy_id)
                continue
            except (ProviderError, ValueError) as exc:
                logger.warning("No se pudo traer el pedido %s de la tienda %s para imprimir: %s", legacy_id, connection.pk, exc)
                raise PrintError("No pudimos traer los pedidos de la tienda. Probá de nuevo en unos minutos.") from exc
        orders.append(order)
    return orders, missing


def make_print_token(connection, orders):
    return signing.dumps({"c": connection.pk, "o": [order.pk for order in orders]}, salt=PRINT_TOKEN_SALT)


def read_print_token(token, platform=StoreConnection.Platform.SHOPIFY):
    """``(connection, orders)`` del enlace, o ``PrintError`` si venció o fue
    alterado. Revalida la tienda: una desconectada ya no imprime. También lo
    usa la impresión desde WooCommerce (``woocommerce_print``), con su
    ``platform``: un enlace de una plataforma no sirve en la ruta de otra."""
    max_age = getattr(settings, "SHOPIFY_PRINT_LINK_MAX_AGE_SECONDS", 900)
    try:
        data = signing.loads(token, salt=PRINT_TOKEN_SALT, max_age=max_age)
    except signing.BadSignature as exc:
        raise PrintError("El enlace de impresión venció. Volvé a elegir los pedidos en tu tienda.") from exc
    connection = StoreConnection.objects.filter(
        pk=data.get("c"), platform=platform, status=StoreConnection.Status.ACTIVE
    ).first()
    if connection is None:
        raise PrintError("La tienda ya no está conectada.")
    by_id = {
        order.pk: order
        for order in Order.objects.filter(pk__in=data.get("o") or [], store_connection=connection).select_related(
            "address", "store_connection"
        )
    }
    orders = [by_id[pk] for pk in data.get("o") or [] if pk in by_id]
    if not orders:
        raise PrintError("Los pedidos de este enlace ya no existen.")
    return connection, orders


def print_url(token, route="shopify-print"):
    base_url = str(getattr(settings, "INTEGRATIONS_PUBLIC_BASE_URL", "") or "").rstrip("/")
    if not base_url:
        raise PrintError("Falta la URL pública del servidor (INTEGRATIONS_PUBLIC_BASE_URL).")
    return f"{base_url}{reverse(route, kwargs={'token': token})}"


def render_pdf(connection, orders):
    """PDF con un rótulo por pedido: la plantilla de la tienda (o la pública
    por defecto) y su logo, como el lote de ``imprimir_rotulos.html``."""
    template = resolve_template(connection)
    if template is None:
        raise PrintError("No hay ninguna plantilla de rótulo disponible.")
    pdf_bytes, count, skipped = _render_combined_pdf(orders, template, owner=connection.owner)
    if skipped:
        logger.warning("Impresión Shopify de la tienda %s: rótulos omitidos %s", connection.pk, skipped)
    if not count:
        raise PrintError("No se pudo dibujar ningún rótulo.")
    return pdf_bytes, count
