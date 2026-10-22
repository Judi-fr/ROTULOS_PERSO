"""Todo lo de Magento 2 / Adobe Commerce (fase 1), en un solo lugar:

- ``provider.py``: ``MagentoProvider`` — pedidos (importación y repaso por
  ``updated_at``) y despacho (shipment con seguimiento).
- ``oauth.py``: la firma OAuth 1.0a de cada pedido a la API.
- ``views.py``: ``POST magento/connect-manual/`` (las credenciales de una
  Integración que pega el comerciante) y lo que llama nuestro módulo:
  ``print-link/``, ``print/<token>``, ``rates/`` y ``module/`` (el zip).
- ``admin_module.py`` + ``magento_module/`` (fase 2): nuestro módulo
  "Rotulos_Envio" en PHP — avisos de pedidos al instante, acciones masivas en
  Ventas → Pedidos (imprimir, planilla, despachar) y cotización en el checkout.
- ``tests/``: contra un Magento simulado que verifica la firma.

Fuera de esta carpeta quedan solo las piezas que van en lugares fijos: la
opción ``StoreConnection.Platform.MAGENTO`` y su migración, los ``MAGENTO_*``
de ``config/settings/base.py`` y el formulario en ``frontend/tiendas.html``
(su JS en ``frontend/assets/js/magento/``). Para probarlo en una tienda real
sin servidor propio: ``magento-prueba/`` en la raíz del repo (Codespaces).
"""

from .provider import MagentoProvider

__all__ = ["MagentoProvider"]
