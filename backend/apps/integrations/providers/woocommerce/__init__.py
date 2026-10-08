"""Todo lo de WooCommerce, en un solo lugar:

- ``provider.py``: ``WooCommerceProvider`` — claves de API, webhooks por
  tienda, pedidos, repaso, despacho con nota y la configuración del plugin.
- ``admin_print.py``: el pedido firmado del plugin y el zip del plugin.
- ``rates.py``: la cotización que pide el plugin desde el checkout.
- ``views.py``: autorización automática y manual, y lo que llama el plugin.
- ``wordpress_plugin/rotulos-envio/``: nuestro plugin de WordPress (dentro
  del backend porque la imagen de Docker solo copia ``backend/``).
- ``tests/``.

Fuera de esta carpeta: la opción ``Platform.WOOCOMMERCE`` y su migración, los
``WOOCOMMERCE_*`` de la configuración y su JS en
``frontend/assets/js/woocommerce/``.
"""

from .provider import WooCommerceProvider

__all__ = ["WooCommerceProvider"]
