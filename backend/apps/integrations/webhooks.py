"""Envío de webhooks SALIENTES (story 24): cuando un pedido cambia de
estado, avisar al sistema del cliente.

Sin cola de tareas (ver CLAUDE.md): el envío es síncrono, en el mismo
request que disparó el cambio, con un timeout corto
(``WEBHOOK_DELIVERY_TIMEOUT_SECONDS``). Un fallo de entrega (timeout, 4xx,
5xx, DNS roto, lo que sea) NUNCA debe romper esa operación — se registra
en ``WebhookDelivery`` y la ejecución sigue su curso; ``dispatch_event``
es la única función que llama a esto, y no deja escapar ninguna excepción.

``dispatch_event`` se llama desde dos lugares:

- ``apps.orders.models.Order.save()``, un pedido a la vez (alta o cambio
  de estado) — ahí el envío síncrono no es un problema, es una sola
  llamada HTTP por request.
- Las entradas por lote (importación CSV/Excel en
  ``apps.orders.import_views``, API de ingesta y webhook entrante en
  ``apps.integrations.views``), que envuelven la creación de todos los
  pedidos del lote en ``webhooks_suspended()`` para no encadenar cientos
  de llamadas HTTP en un solo request, y mandan un único evento
  ``orders.imported`` con el resumen al terminar.
"""

from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import logging
from contextlib import contextmanager

import requests
from django.conf import settings

from .models import WebhookDelivery, WebhookEndpoint

logger = logging.getLogger(__name__)

# Activo (True) mientras una operación por lote está en curso: dispatch_event
# se vuelve un no-op para no encadenar una llamada HTTP por fila. Un
# ContextVar (no un flag global) para que quede acotado al contexto de
# ejecución actual y no se filtre entre requests concurrentes.
_suspended = contextvars.ContextVar("webhooks_suspended", default=False)


@contextmanager
def webhooks_suspended():
    """Silencia ``dispatch_event`` para toda operación dentro de este
    bloque (p. ej. las 1000 filas de una importación): en vez de una
    llamada HTTP por pedido, quien use esto manda un único evento resumen
    (``orders.imported``) al terminar. Es la única forma de suprimir
    webhooks salientes en la app — no agregar un flag por instancia
    aparte."""
    token = _suspended.set(True)
    try:
        yield
    finally:
        _suspended.reset(token)


def sign_payload(secret, body_bytes):
    return hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()


def send_webhook(endpoint, event, payload):
    """Un único intento de entrega. Guarda un ``WebhookDelivery`` pase lo
    que pase (éxito, error HTTP o excepción de red): es el registro que
    permite mostrar el historial y, más adelante, reintentar sobre esto."""
    body = json.dumps(payload, default=str).encode("utf-8")
    signature = sign_payload(endpoint.secret, body)
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event": event,
        "X-Webhook-Signature": signature,
    }
    timeout = getattr(settings, "WEBHOOK_DELIVERY_TIMEOUT_SECONDS", 3)

    try:
        response = requests.post(endpoint.url, data=body, headers=headers, timeout=timeout)
        WebhookDelivery.objects.create(
            endpoint=endpoint,
            event=event,
            payload=payload,
            status_code=response.status_code,
            success=response.ok,
            response_body=response.text[:2000],
        )
    except requests.RequestException as exc:
        WebhookDelivery.objects.create(
            endpoint=endpoint,
            event=event,
            payload=payload,
            status_code=None,
            success=False,
            response_body=str(exc)[:2000],
        )


def dispatch_event(owner, event, payload):
    """Notifica a todos los ``WebhookEndpoint`` activos de ``owner``
    suscriptos a ``event`` (``events`` vacío = suscripto a todos).

    Ver el docstring del módulo para quién la llama. No-op mientras
    ``webhooks_suspended()`` esté activo, y nunca deja propagar una
    excepción, para que un webhook mal configurado no pueda romper la
    operación que lo disparó.
    """
    if owner is None or _suspended.get():
        return
    try:
        endpoints = list(WebhookEndpoint.objects.filter(owner=owner, is_active=True))
    except Exception:  # noqa: BLE001 - ni una falla de DB acá tumba el save()
        logger.exception("No se pudo leer los webhooks salientes de %s", owner)
        return

    for endpoint in endpoints:
        if endpoint.events and event not in endpoint.events:
            continue
        try:
            send_webhook(endpoint, event, payload)
        except Exception:  # noqa: BLE001 - un webhook roto no rompe el resto
            logger.exception("Fallo inesperado enviando el webhook %s (%s)", endpoint.pk, event)
            continue
