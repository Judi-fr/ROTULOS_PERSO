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

import base64
import hashlib
import hmac
import io
import json
import time
import zipfile
from pathlib import Path

from ...models import StoreConnection
from ...store_print import PrintError

SIGNATURE_HEADER = "X-Rotulos-Signature"
PLUGIN_DIR = Path(__file__).resolve().parent / "wordpress_plugin" / "rotulos-envio"
PLUGIN_ZIP_NAME = f"{PLUGIN_DIR.name}.zip"
# Margen para el reloj del hosting de la tienda, que no siempre está en hora.
MAX_CLOCK_SKEW_SECONDS = 300


def verify_plugin_request(raw_body, signature, now=None):
    """``(connection, data)`` de un pedido firmado del plugin (imprimir o
    cotizar), o ``PrintError``. La tienda sale del cuerpo, pero solo vale si
    la firma coincide con SU secreto. El resto de ``data`` lo valida quien lo
    usa (``rates``)."""
    try:
        data = json.loads(raw_body or b"{}")
    except ValueError as exc:
        raise PrintError("El pedido de impresión no es válido.") from exc
    if not isinstance(data, dict):
        raise PrintError("El pedido de impresión no es válido.")

    store_id = str(data.get("store") or "").strip()
    connection = None
    if store_id.isdigit():
        connection = StoreConnection.objects.filter(
            pk=int(store_id), platform=StoreConnection.Platform.WOOCOMMERCE
        ).first()
    secret = connection.webhook_secret if connection is not None else ""
    expected = base64.b64encode(hmac.new(secret.encode("utf-8"), raw_body or b"", hashlib.sha256).digest())
    if not secret or not signature or not hmac.compare_digest(signature.strip().encode("utf-8"), expected):
        raise PrintError("El plugin no está vinculado con esta tienda. Volvé a conectarla desde la app.")

    try:
        sent_at = int(data.get("ts"))
    except (TypeError, ValueError):
        sent_at = 0
    if abs((now or time.time()) - sent_at) > MAX_CLOCK_SKEW_SECONDS:
        raise PrintError("El pedido de impresión venció. Revisá que el reloj del servidor de la tienda esté en hora.")

    if connection.status != StoreConnection.Status.ACTIVE:
        raise PrintError("Esta tienda está desconectada de la app. Volvé a conectarla para imprimir.")
    if connection.owner_id is None:
        raise PrintError("Esta tienda todavía no está vinculada a una cuenta de la app.")
    return connection, data


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
