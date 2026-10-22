"""Rótulos impresos desde el admin de WooCommerce (plugin "Rótulos de envío").

El comerciante tilda pedidos en WooCommerce → Pedidos y elige la acción
masiva "Imprimir rótulos". Esa acción la agrega nuestro plugin de WordPress
(``wordpress_plugin/rotulos-envio/``), que corre en el servidor de la tienda:

1. ``POST /woocommerce/print-link/`` — el plugin lo llama de servidor a
   servidor con ``{"store", "ids", "ts"}``, firmado (HMAC-SHA256 en base64,
   header ``X-Rotulos-Signature``) con el secreto de esa tienda: el mismo
   ``StoreConnection.webhook_secret`` con el que la tienda firma sus
   webhooks, así que la confianza es la misma. ``ts`` acota la repetición de
   un pedido capturado. Devuelve el enlace de vida corta al PDF.
2. ``GET /woocommerce/print/<token>`` — el plugin manda el navegador ahí. Lo
   autentica el enlace firmado, como en Shopify.

El comerciante no configura nada: la URL, el número de tienda y el secreto se
los escribe el worker al plugin por la API de WooCommerce
(``WooCommerceProvider.configure_admin_print``) al conectar la tienda y en
cada repaso. Resolver pedidos, firmar el enlace y dibujar el PDF es lo mismo
que en Shopify y Tiendanube (``apps.integrations.store_print``). El mismo plugin cotiza el envío en el
checkout con la misma firma (``rates``).

El plugin vive en ``wordpress_plugin/rotulos-envio/`` (dentro del backend:
la imagen de Docker solo copia ``backend/``) y el comerciante lo baja como
zip desde ``tiendas.html`` (``plugin_zip``).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from ...models import StoreConnection
from ...store_print import MAX_CLOCK_SKEW_SECONDS, SIGNATURE_HEADER, PrintError, verify_signed_request

__all__ = ["MAX_CLOCK_SKEW_SECONDS", "SIGNATURE_HEADER", "PrintError", "plugin_zip", "verify_plugin_request"]

PLUGIN_DIR = Path(__file__).resolve().parent / "wordpress_plugin" / "rotulos-envio"
PLUGIN_ZIP_NAME = f"{PLUGIN_DIR.name}.zip"


def verify_plugin_request(raw_body, signature, now=None):
    """``(connection, data)`` de un pedido firmado del plugin (imprimir o
    cotizar), o ``PrintError``. La verificación es la compartida con el módulo
    de Magento (``store_print.verify_signed_request``)."""
    return verify_signed_request(raw_body, signature, StoreConnection.Platform.WOOCOMMERCE, now=now)


def plugin_zip():
    """El plugin como lo instala WordPress (Plugins → Añadir nuevo → Subir):
    un zip con la carpeta ``rotulos-envio/`` adentro. Esa carpeta es la que
    WordPress usa como identidad del plugin; con otro nombre, una versión
    nueva se instalaría al lado de la vieja en vez de reemplazarla."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(PLUGIN_DIR.rglob("*")):
            if path.is_file():
                archive.write(path, Path(PLUGIN_DIR.name) / path.relative_to(PLUGIN_DIR))
    return buffer.getvalue()
