"""Todo lo de Shopify, en un solo lugar:

- ``provider.py``: ``ShopifyProvider`` — OAuth con tokens que vencen,
  GraphQL, webhooks, pedidos, despacho y eventos de seguimiento.
- ``admin_print.py``: lo propio de imprimir desde su menú Imprimir (el ID
  token de la extensión). Lo común está en ``apps.integrations.store_print``.
- ``views.py``: la App URL (``launch``), la conexión con la app propia del
  comerciante y la impresión.
- ``tests/``.

Fuera de esta carpeta: la opción ``Platform.SHOPIFY`` y sus migraciones, los
``SHOPIFY_*`` de la configuración, el proyecto de la extensión de impresión
(``rotulos-extension/`` en la raíz del repo, lo maneja la CLI de Shopify) y su
JS en ``frontend/assets/js/shopify/``.
"""

from .provider import ShopifyProvider

__all__ = ["ShopifyProvider"]
