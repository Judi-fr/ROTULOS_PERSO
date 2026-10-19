"""Monitoreo y alertas de disponibilidad (T#193).

- ``checks.py``: qué se mira (base, worker, cola de integraciones, lotes de
  rótulos, disco, mail de alertas, tiendas y transportistas con error).
- ``heartbeats.py``: cada proceso de fondo deja su "latido" en la base.
- ``alerts.py``: el mail a los admins cuando un chequeo pasa a error, un
  recordatorio si sigue, y otro cuando se resuelve.
- ``management/commands/run_monitor.py``: el proceso que corre las alertas.
  El worker de integraciones lo reemplaza si el monitor está caído.
"""
