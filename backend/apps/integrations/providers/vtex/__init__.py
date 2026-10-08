"""Todo lo de VTEX, en un solo lugar:

- ``provider.py``: ``VtexProvider`` — conexión con appKey/appToken, pedidos
  (importación, hook y feed), despacho con el seguimiento en la factura.
- ``freight.py``: la cotización del checkout (la tabla de tarifas publicada
  como tablas de flete de VTEX).
- ``views.py``: ``POST vtex/connect-manual/`` (la clave que pega el comerciante).
- ``common.py``: helpers compartidos por los dos primeros.
- ``tests/``: contra una VTEX simulada (``tests/fake_vtex.py``).

Fuera de esta carpeta quedan solo las piezas que van en lugares fijos: la
opción ``StoreConnection.Platform.VTEX`` y su migración, los ``VTEX_*`` de
``config/settings/base.py``, el formulario en ``frontend/tiendas.html`` y la
tarjeta de ``frontend/tarifas_envio.html`` (su JS en ``frontend/assets/js/vtex/``).
"""

from .provider import VtexProvider

__all__ = ["VtexProvider"]
