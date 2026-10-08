"""Todo lo de Tiendanube, en un solo lugar:

- ``provider.py``: ``TiendanubeProvider`` — OAuth, webhooks, pedidos,
  despacho (fulfillment orders) y la API de carriers/rótulos.
- ``labels.py``: los rótulos que la tienda nos pide desde su admin (Labels
  API); ``label_views.py``/``label_urls.py`` sus callbacks públicos;
  ``handlers.py`` la generación en el worker.
- ``rates.py`` + ``rate_views.py``/``rate_urls.py``: la cotización en su
  checkout (el callback de carrier).
- ``views.py``: imprimir desde el link de acciones masivas de Ventas.
- ``tests/``.

Fuera de esta carpeta, en lugares fijos: la opción ``Platform.TIENDANUBE`` y
sus migraciones, los ``TIENDANUBE_*`` de la configuración, el comando
``register_store_carrier`` (Django los busca en ``management/commands``) y la
página ``frontend/imprimir_tiendanube.html`` (su URL está registrada en el
Portal de Partners; su JS en ``frontend/assets/js/tiendanube/``). La
instalación OAuth y el link para compartir son genéricos
(``apps.integrations.views``).
"""

from .provider import TiendanubeProvider

__all__ = ["TiendanubeProvider"]
