"""Credenciales y webhooks para que un sistema externo (ERP, tienda) entre
o salga de la app sin usuario/contraseña (stories 23-24).

Dos direcciones, cuatro modelos:

- Entrante por API: ``IntegrationKey`` (credencial de máquina) +
  ``authentication.ApiKeyAuthentication``, usada por
  ``views.IngestOrderView`` (``POST /api/v1/ingest/orders/``).
- Entrante por webhook: ``IncomingWebhook`` (un endpoint propio por
  slug, firmado con HMAC), usada por ``views.IncomingWebhookView``
  (``POST /api/v1/ingest/webhooks/<slug>/``).
- Saliente: ``WebhookEndpoint`` (a dónde avisar) + ``WebhookDelivery``
  (el registro de cada intento), usados por ``webhooks.dispatch_event``
  cuando un pedido cambia de estado (ver ``apps.orders.models.Order.save``).
"""

from __future__ import annotations

import hashlib
import secrets

from django.conf import settings
from django.db import models


class IntegrationKey(models.Model):
    """Credencial de máquina: identifica a un ERP/tienda externo y a qué
    usuario (``owner``) le pertenecen los pedidos que entren con ella.

    Solo se guarda el HASH (``key_hash``, SHA-256): la clave completa se
    genera y se muestra UNA sola vez, al crearla (ver
    ``views.IntegrationKeyViewSet.create``) — igual criterio que una
    contraseña, nunca se puede "recuperar", solo revocar y generar otra.
    ``prefix`` (los primeros caracteres de la clave, no secretos) es lo
    único que se vuelve a mostrar después, para poder identificarla en
    pantalla sin exponer el resto.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="integration_keys",
    )
    name = models.CharField(max_length=100)
    key_hash = models.CharField(max_length=64, unique=True)
    prefix = models.CharField(max_length=12, db_index=True)
    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "clave de integración"
        verbose_name_plural = "claves de integración"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    @staticmethod
    def generate_raw_key():
        """Clave completa en texto plano — se muestra una sola vez al
        crearla, nunca se persiste tal cual (ver ``hash_key``)."""
        return f"bp_{secrets.token_urlsafe(32)}"

    @staticmethod
    def hash_key(raw_key):
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class IncomingWebhook(models.Model):
    """Un endpoint propio (``/api/v1/ingest/webhooks/<slug>/``) que un
    ERP/tienda externo configura de SU lado para avisarnos de un pedido
    nuevo. ``secret`` firma el payload (HMAC-SHA256, ver
    ``views.IncomingWebhookView``); ``mapping`` reusa el mismo formato que
    ``apps.orders.ingestion`` (campo_destino -> clave del payload): el
    problema es el mismo que en la importación, traducir campos ajenos a
    los propios.
    """

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="incoming_webhooks",
    )
    integration_key = models.ForeignKey(
        IntegrationKey,
        on_delete=models.CASCADE,
        related_name="incoming_webhooks",
    )
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=60, unique=True)
    secret = models.CharField(max_length=64)
    mapping = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "webhook entrante"
        verbose_name_plural = "webhooks entrantes"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} (/{self.slug}/)"

    @staticmethod
    def generate_secret():
        return secrets.token_hex(32)


class WebhookEndpoint(models.Model):
    """A dónde avisar cuando un pedido de ``owner`` cambia de estado
    (webhook SALIENTE). ``events`` vacío = todos los eventos; con una
    lista, solo esos (ver ``webhooks.dispatch_event``)."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="webhook_endpoints",
    )
    name = models.CharField(max_length=100, blank=True, default="")
    url = models.URLField()
    secret = models.CharField(max_length=64)
    events = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "webhook saliente"
        verbose_name_plural = "webhooks salientes"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name or self.url} ({self.owner.email})"

    @staticmethod
    def generate_secret():
        return secrets.token_hex(32)


class WebhookDelivery(models.Model):
    """Registro de UN intento de entrega de un webhook saliente. Se
    guarda siempre (éxito o fallo): es lo que permite mostrar "se
    entregó"/"falló" y, más adelante, agregar reintento sobre esto sin
    cambiar el modelo."""

    endpoint = models.ForeignKey(
        WebhookEndpoint,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    event = models.CharField(max_length=50)
    payload = models.JSONField(default=dict, blank=True)
    attempt = models.PositiveIntegerField(default=1)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    success = models.BooleanField(default=False)
    response_body = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "entrega de webhook"
        verbose_name_plural = "entregas de webhook"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event} -> {self.endpoint_id} ({'ok' if self.success else 'falló'})"
