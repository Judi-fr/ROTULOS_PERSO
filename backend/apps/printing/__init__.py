"""Impresión directa: cola de trabajos e impresoras del comerciante (F6.2,
T#322-T#328).

Un trabajo (``PrintJob``) es un ``apps.documents.Document`` ya generado (el
mismo lote de ``POST /labels/batch/``, que resuelve plantilla, tienda y
densidad) mandado a una ``Printer``. Nuestro servidor no llega a la impresora
del comerciante: la tiene en SU red. Por eso hay un **agente**
(``agent/rotulos_print_agent.py``, ``PrintAgent``) que corre en una PC de esa
red, le pide trabajos a la API con su token, los manda a la impresora e
informa cómo terminó.

- ``models.py``: ``PrintAgent``, ``Printer``, ``PrintJob``.
- ``services.py``: crear, tomar (el agente), terminar, reintentar, cancelar.
- ``views.py``: la API del comerciante (``labels.batch``).
- ``agent_views.py``: la API del agente (``Authorization: Agent <token>``).
- ``agent/``: el programa del agente, que se descarga desde ``impresoras.html``.
"""
