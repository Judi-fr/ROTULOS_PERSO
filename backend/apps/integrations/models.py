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
- Tiendas online conectadas (Tiendanube, y a futuro otras plataformas):
  ``StoreConnection`` (la tienda y su token OAuth cifrado) +
  ``IntegrationEvent`` (cola persistente con reintentos que procesa
  ``manage.py run_integrations_worker``, ver ``events``) +
  ``StoreLabelRequest`` (un rótulo que pidió la tienda desde SU admin, ver
  ``store_labels``).
"""

from __future__ import annotations

import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from . import crypto


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


class StoreConnection(models.Model):
    """Una tienda online conectada por un cliente para que sus pedidos
    entren solos (hoy Tiendanube; cada plataforma nueva es un proveedor más
    en ``apps.integrations.providers``, no un modelo nuevo).

    Todo lo que viene de una tienda se aísla por CONEXIÓN, no por usuario:
    la idempotencia de sus pedidos (``Order.store_connection`` +
    ``external_id``) y su cola de eventos (``IntegrationEvent``). Hoy la
    conexión pertenece a un usuario (``owner``); si los clientes pasan a
    ser empresas con varios usuarios, cambia solo ese FK.

    No se borra: al desconectar pasa a ``status=revoked`` y sus pedidos
    siguen apuntándole (``Order.store_connection`` es RESTRICT).

    ``owner`` vacío = tienda instalada desde la tienda de apps de la
    plataforma por alguien que todavía no la vinculó a su cuenta (ver
    ``apps.integrations.stores.claim_store``); hasta entonces no entran
    pedidos.
    """

    class Platform(models.TextChoices):
        TIENDANUBE = "tiendanube", "Tiendanube"

    class Status(models.TextChoices):
        ACTIVE = "active", "Conectada"
        REVOKED = "revoked", "Desconectada"
        ERROR = "error", "Con errores"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="store_connections",
    )
    platform = models.CharField(max_length=30, choices=Platform.choices)
    # Id de la tienda en la plataforma (en Tiendanube, el ``user_id`` que
    # devuelve el canje del código OAuth).
    external_store_id = models.CharField(max_length=64)
    name = models.CharField(max_length=150, blank=True, default="")
    store_url = models.URLField(blank=True, default="")
    # Cifrado con apps.integrations.crypto: leer/escribir vía ``access_token``.
    access_token_encrypted = models.TextField(blank=True, default="")
    scopes = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    last_error = models.TextField(blank=True, default="")
    # Remitente de los rótulos de ESTA tienda: lo que se imprime como quien
    # despacha el paquete. La app es multi-cliente y un mismo usuario puede
    # tener varias tiendas, así que el remitente vive acá y no en settings
    # ni en el usuario. Vacío = se usa el nombre de la tienda (ver
    # apps.labels.label_rendering.build_label_context).
    sender_name = models.CharField(max_length=150, blank=True, default="")
    sender_address = models.CharField(max_length=255, blank=True, default="")
    sender_phone = models.CharField(max_length=50, blank=True, default="")
    # Logo que se imprime en los rótulos de esta tienda (el diseño decide
    # DÓNDE, con su clave "logo"; acá solo vive la imagen). Vacío = rótulo
    # sin logo, como hasta ahora.
    logo = models.ImageField(upload_to="store_logos/%Y/%m/", blank=True, null=True)
    # Plantilla preferida para los rótulos de esta tienda. SET_NULL: si la
    # plantilla se borra, la tienda vuelve a la plantilla por defecto, nunca
    # se cae la conexión.
    default_template = models.ForeignKey(
        "labels.LabelTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="stores",
    )
    # Preferencias por tienda (p. ej. qué pedidos importar). JSON y no
    # columnas porque se van a ir definiendo con cada plataforma.
    preferences = models.JSONField(default=dict, blank=True)
    connected_at = models.DateTimeField(default=timezone.now)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "tienda conectada"
        verbose_name_plural = "tiendas conectadas"
        ordering = ["-created_at"]
        constraints = [
            # Una tienda instala la app una sola vez: reconectarla tiene que
            # actualizar esta fila (token nuevo), no crear otra que duplique
            # todos sus pedidos.
            models.UniqueConstraint(
                fields=["platform", "external_store_id"], name="unique_store_per_platform"
            )
        ]

    def __str__(self):
        return f"{self.get_platform_display()} {self.name or self.external_store_id}"

    @property
    def access_token(self):
        return crypto.decrypt(self.access_token_encrypted)

    @access_token.setter
    def access_token(self, value):
        self.access_token_encrypted = crypto.encrypt(value)

    @property
    def is_active(self):
        return self.status == self.Status.ACTIVE


class IntegrationEvent(models.Model):
    """Cola persistente del trabajo de integraciones con tiendas.

    Tiendanube exige responder un webhook en menos de 3 segundos y, si no,
    lo reintenta: el receptor solo verifica la firma, encola acá y responde;
    el trabajo real (pedirle el pedido a la API, crearlo o actualizarlo) lo
    hace ``manage.py run_integrations_worker`` con reintentos y backoff (ver
    ``apps.integrations.events``). Sin Celery/Redis: la cola es esta tabla.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        PROCESSING = "processing", "Procesando"
        DONE = "done", "Procesado"
        FAILED = "failed", "Falló"

    # SET_NULL: un evento de una tienda que no reconocemos (o ya
    # desconectada) igual queda registrado para depurar.
    connection = models.ForeignKey(
        StoreConnection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    platform = models.CharField(max_length=30, choices=StoreConnection.Platform.choices)
    event_type = models.CharField(max_length=80)
    resource_id = models.CharField(max_length=100, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    # Hash del evento: el mismo aviso repetido mientras el primero sigue
    # pendiente no se encola dos veces (ver events.enqueue_event).
    dedupe_key = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True, default="")
    # Lo que produjo el handler cuando hay algo que conservar (p. ej. el
    # reporte de un pedido de datos de privacidad, ver apps.integrations.privacy).
    result = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "evento de integración"
        verbose_name_plural = "eventos de integración"
        ordering = ["created_at"]
        indexes = [models.Index(fields=["status", "next_attempt_at"], name="integ_event_due_idx")]

    def __str__(self):
        return f"{self.platform} {self.event_type} #{self.resource_id or '-'} ({self.status})"


# ---------------------------------------------------------------------------
# StoreLabelRequest: un rótulo que pidió la TIENDA, no el comerciante desde
# nuestra app. Convive con apps.labels.Label (el rótulo propio, guardado y
# reimprimible) — son conceptos distintos y no se mezclan.
# ---------------------------------------------------------------------------


class StoreLabelRequest(models.Model):
    """Un pedido de rótulo que entra por la Labels API de la plataforma.

    El comerciante tilda pedidos en el admin de SU tienda y pide las
    etiquetas; la plataforma nos llama al callback, nosotros generamos el
    PDF fuera del request (cola de ``IntegrationEvent``) y le avisamos
    dónde bajarlo. Esta fila es lo que hace que ese ida y vuelta sea
    rastreable: qué nos pidieron, qué generamos y por qué falló.

    ``external_label_id`` es único por tienda: el callback repetido (la
    plataforma reintenta) encuentra la fila que ya existe y no vuelve a
    generar nada.

    ``download_token`` es la única llave de la descarga: la plataforma se
    baja el PDF sin sesión, así que el token es aleatorio, largo y de vida
    corta — se vacía apenas la plataforma confirma que ya tiene el archivo
    (ver ``store_labels.release_download``).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        READY = "ready", "Generado"
        FAILED = "failed", "Falló"
        CANCELED = "canceled", "Cancelado"
        SUSPENDED = "suspended", "Suspendido"

    connection = models.ForeignKey(
        StoreConnection,
        on_delete=models.CASCADE,
        related_name="label_requests",
    )
    # Ids de la plataforma (en Tiendanube, ULIDs). El PATCH de vuelta se
    # arma con los dos: /fulfillment-orders/<ffo>/labels/<label>.
    external_label_id = models.CharField(max_length=64)
    external_fulfillment_order_id = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # El elemento del callback tal cual llegó: trae el envío completo
    # (destinatario, domicilio, opción de envío), que es de donde sale el
    # rótulo. Sin esto habría que volver a pedirle el envío a la API.
    payload = models.JSONField(default=dict, blank=True)
    file = models.FileField(upload_to="store_labels/%Y/%m/", null=True, blank=True)
    download_token = models.CharField(max_length=64, blank=True, default="", db_index=True)
    # Qué falló, en castellano: es lo que se le muestra al comerciante
    # cuando pregunta por qué no le salió la etiqueta.
    error_message = models.TextField(blank=True, default="")
    # Cuándo la plataforma confirmó que ya tiene el archivo (y se invalidó
    # el token). Vacío = todavía lo servimos nosotros.
    released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "rótulo pedido por la tienda"
        verbose_name_plural = "rótulos pedidos por la tienda"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "external_label_id"], name="unique_store_label_request"
            )
        ]

    def __str__(self):
        return f"{self.connection} · etiqueta {self.external_label_id} ({self.status})"
