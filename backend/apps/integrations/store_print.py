"""Rótulos impresos desde el admin de la propia tienda: lo común a Shopify
(menú Imprimir), WooCommerce (nuestro plugin) y Tiendanube (link de acciones
masivas).

Dos pasos, a propósito:

1. ``POST <plataforma>/print-link/`` — lo llama la tienda (o nuestra página,
   en Tiendanube) autenticada a su manera (ver la carpeta de cada plataforma):
   se resuelven los pedidos elegidos (``resolve_orders``) y se devuelve un
   enlace firmado y de vida corta (``make_print_token`` + ``print_url``).
2. ``GET <plataforma>/print/<token>`` — el enlace: lo carga el navegador o la
   vista previa de impresión, que no lleva credenciales. Lo autentica el
   propio enlace (``read_print_token``) y se dibuja el PDF (``render_pdf``).

Un pedido elegido que todavía no tenemos (más viejo que la importación
inicial, o un aviso que se perdió) se trae en el momento: el comerciante eligió
imprimirlo, no puede faltar del PDF.

Nació como la impresión de Shopify (de ahí ``PRINT_TOKEN_SALT``, que no se
cambia para no invalidar enlaces, y ``SHOPIFY_PRINT_LINK_MAX_AGE_SECONDS``).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core import signing
from django.urls import reverse

from apps.labels.batch_views import _render_combined_pdf
from apps.labels.models import LabelTemplate
from apps.orders.ingestion import upsert_store_order
from apps.orders.models import Order

from .models import StoreConnection
from .providers import get_provider
from .providers.base import ProviderError, ProviderNotFoundError

logger = logging.getLogger(__name__)

PRINT_TOKEN_SALT = "apps.integrations.shopify-print"
# Lo que Shopify deja seleccionar de una vez en la lista de pedidos ronda los
# 250; más que eso es un pedido raro o malicioso, y cada uno es una página.
MAX_ORDERS_PER_PRINT = 250


class PrintError(Exception):
    """No se puede armar la impresión; el mensaje es para el comerciante."""


def resolve_template(connection):
    """Plantilla con la que se dibuja este rótulo: la que la tienda eligió
    en ``tiendas.html`` si sigue siendo usable, o la pública por defecto (la
    más antigua activa). ``None`` si no hay ninguna."""
    template = connection.default_template
    if template is not None and template.is_active:
        if template.is_public or template.owner_id == connection.owner_id:
            return template
    return LabelTemplate.objects.filter(is_public=True, is_active=True).order_by("id").first()


def order_ids(selected):
    """Ids numéricos de pedido como texto, sin repetir y en el orden elegido
    (es el orden de las páginas del PDF). Lo usan WooCommerce y Tiendanube;
    Shopify manda GIDs (``shopify/admin_print.legacy_order_ids``)."""
    if not isinstance(selected, list) or not selected:
        raise PrintError("No llegó ningún pedido para imprimir.")
    if len(selected) > MAX_ORDERS_PER_PRINT:
        raise PrintError(f"Se pueden imprimir hasta {MAX_ORDERS_PER_PRINT} pedidos por vez.")
    ids = []
    for value in selected:
        order_id = str(value).strip()
        if not order_id.isdigit():
            raise PrintError("Uno de los pedidos elegidos no es válido.")
        if order_id not in ids:
            ids.append(order_id)
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
    alterado. Revalida la tienda: una desconectada ya no imprime. Cada
    plataforma lo lee con su ``platform``: un enlace de una plataforma no
    sirve en la ruta de otra."""
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
