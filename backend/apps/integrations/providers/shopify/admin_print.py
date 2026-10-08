"""Impresión desde el admin de Shopify: lo propio de Shopify. Lo común
(resolver pedidos, el enlace firmado, el PDF) está en
``apps.integrations.store_print``.

El comerciante tilda pedidos en SU lista de pedidos de Shopify, abre el menú
"Imprimir" y elige nuestros rótulos. Esa opción es una *admin print action
extension* (el proyecto de la extensión vive aparte, ver ``rotulos-extension/``
en la raíz del repo): la extensión llama a ``POST /shopify/print-link/`` con
``fetch``, y Shopify le agrega solo un *ID token* (JWT HS256 firmado con el
client secret de la app; ``verify_id_token``). Ese token dice QUÉ tienda es.

Separar el enlace del ID token evita depender de cómo la vista previa carga
``src`` (la documentación no dice si le agrega credenciales) y de que el ID
token, que dura un minuto, siga vivo cuando el comerciante aprieta "Imprimir".
"""

from __future__ import annotations

from urllib.parse import urlparse

import jwt
from django.conf import settings

from ...models import StoreConnection
from ...store_print import MAX_ORDERS_PER_PRINT, PrintError
from .. import get_provider


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
