"""Todo lo de Correo Argentino, en un solo lugar:

- ``client.py``: sus dos APIs — Paq.ar (alta, rótulo, seguimiento, sucursales,
  cancelación) y MiCorreo (solo cotizar). Escrito sin credenciales, desde sus
  manuales oficiales: lo dudoso dice "A CONFIRMAR".
- ``provider.py``: Correo Argentino en la capa común (``apps/carriers/base.py``).
- ``tests/``: contra un Correo simulado (``tests/fake_correo.py``).

Fuera de esta carpeta: los modelos (``apps/carriers/models.py``, compartidos),
las vistas comunes (``apps/carriers/views.py``), los ``CORREO_ARGENTINO_*`` de
la configuración y el frontend común de transportistas (``transportista.html``,
``assets/js/pedidos/despacho.js``).
"""
