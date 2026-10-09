"""Todo lo de Andreani, en un solo lugar (Transporte y Distribución; su servicio
de almacén, Warehouse, queda para más adelante):

- ``client.py``: la API de Andreani (login con token de 24 h, orden de envío,
  etiquetas PDF/ZPL, trazas, sucursales y puntos HOP, cancelación). Escrito sin
  credenciales, desde su documentación oficial: lo dudoso dice "A CONFIRMAR".
- ``shipments.py``: de un pedido a una orden de Andreani (el número de envío
  queda como seguimiento del pedido y es lo que imprime el rótulo debajo del
  código de barras) y de los movimientos de Andreani al estado del pedido.
- ``views.py`` + ``urls.py``: la API para el comerciante (``/api/v1/carriers/andreani/``).
- ``tests/``: contra una Andreani simulada (``tests/fake_andreani.py``).

Fuera de esta carpeta: los modelos y la migración (``apps/carriers/models.py``,
compartidos por todos los transportistas), el seguimiento automático que llama
el worker (``apps/carriers/tracking.py``), los ``ANDREANI_*`` de la
configuración, y el frontend: se despacha desde Mis pedidos
(``frontend/assets/js/pedidos/envios.js`` y ``despacho.js``), y la cuenta y el
precio en el checkout son ``frontend/andreani.html`` y ``checkout_andreani.html``
(JS en ``frontend/assets/js/andreani/``). Para probar sin credenciales:
``manage.py fake_andreani_server``.
"""
