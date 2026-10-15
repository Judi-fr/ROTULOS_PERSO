"""Todo lo de OCA (e-Pak), en un solo lugar:

- ``client.py``: los web services de e-Pak (operativas, cotizar, orden de
  retiro, etiquetas PDF/ZPL, seguimiento, anulación, sucursales). Probado contra
  el QA público de OCA; lo que solo se ve con envíos reales dice "A CONFIRMAR".
- ``provider.py``: OCA en la capa común (``apps/carriers/base.py``): el XML de
  la orden, la traducción de los estados y las sucursales.
- ``tests/``: contra una OCA simulada (``tests/fake_oca.py``).

Fuera de esta carpeta: los modelos (``apps/carriers/models.py``, compartidos),
las vistas comunes (``apps/carriers/views.py``), los ``OCA_*`` de la
configuración y el frontend común de transportistas (``transportista.html``,
``assets/js/pedidos/despacho.js``).
"""
