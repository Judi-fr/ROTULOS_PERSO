"""Todo lo de Empretienda, en un solo lugar. Empretienda no tiene API: los
pedidos entran importando la planilla que exporta su admin.

- ``provider.py``: ``EmpretiendaProvider`` — una tienda sin credenciales ni
  avisos (para que sus pedidos queden agrupados como los de cualquier tienda).
- ``spreadsheet.py``: la planilla de ventas → pedidos (columnas, filas por
  producto, estados).
- ``views.py``: agregar la tienda (``empretienda/connect/``) e importar
  (``empretienda/import/preview/`` + ``confirm/``).
- ``tests/``.

Fuera de esta carpeta: la opción ``Platform.EMPRETIENDA`` y su migración, el
formulario en ``frontend/tiendas.html``, la página
``frontend/importar_empretienda.html`` y su JS en
``frontend/assets/js/empretienda/``. Reutiliza la lectura de CSV/Excel
(``apps.orders.import_parsing``) y la detección de columnas
(``apps.orders.bulk.match_columns``).
"""

from .provider import EmpretiendaProvider

__all__ = ["EmpretiendaProvider"]
