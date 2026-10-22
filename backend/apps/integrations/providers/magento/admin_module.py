"""Nuestro módulo de Magento ("Rotulos_Envio", fase 2), instalado en la tienda.

Hace en Magento lo que el plugin de WordPress hace en WooCommerce
(``woocommerce/admin_print.py``):

- **Avisa cada pedido guardado al instante** (``Observer/OrderSaved.php``):
  ``POST magento/webhooks/?store=<id>`` firmado con el secreto de la tienda.
  Lo recibe el receptor genérico (``views.StoreWebhookView``) y el worker trae
  el pedido por la API, como con un webhook de cualquier otra plataforma. El
  repaso periódico sigue igual, por si un aviso se pierde.
- **Imprime, arma la planilla o despacha desde la grilla de pedidos**
  (acciones masivas, ``Controller/Adminhtml/Order/MassAction.php``): llama de
  servidor a servidor a ``POST magento/print-link/`` y manda el navegador al
  enlace firmado ``GET magento/print/<token>``.
- **Cotiza en el checkout** (``Model/Carrier/Rotulos.php``): ``POST
  magento/rates/``, con la misma tabla y regla que las demás plataformas.

Todo lo que manda el módulo va firmado como el plugin de WooCommerce
(``store_print.verify_signed_request``). El comerciante no configura nada a
mano: el worker le escribe URL, tienda y secreto por la API REST propia del
módulo (``MagentoProvider.configure_admin_print``) al conectar y en cada
repaso. El módulo vive en ``magento_module/`` y se baja como zip desde
``tiendas.html`` (``module_zip``).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from ...models import StoreConnection
from ...store_print import SIGNATURE_HEADER, verify_signed_request

__all__ = ["SIGNATURE_HEADER", "MODULE_ZIP_NAME", "module_zip", "verify_module_request"]

# app/code/Rotulos/Envio: Magento identifica un módulo por esa ruta
# (Vendor/Módulo), así que el zip la trae armada.
MODULE_DIR = Path(__file__).resolve().parent / "magento_module"
MODULE_ZIP_NAME = "rotulos-envio-magento.zip"


def verify_module_request(raw_body, signature, now=None):
    """``(connection, data)`` de un pedido firmado del módulo, o ``PrintError``."""
    return verify_signed_request(raw_body, signature, StoreConnection.Platform.MAGENTO, addon="módulo", now=now)


def module_zip():
    """El módulo listo para descomprimir en la raíz de Magento: trae
    ``app/code/Rotulos/Envio/...``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(MODULE_DIR.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, Path("app") / "code" / "Rotulos" / "Envio" / path.relative_to(MODULE_DIR))
    return buffer.getvalue()
