"""Transportistas (correos) con los que despachan los clientes: Andreani, Correo
Argentino y OCA.

Es lo opuesto a ``apps.integrations``: ahí la tienda nos manda pedidos; acá
nosotros le pedimos al CORREO que los lleve. Cada cliente usa SU cuenta y SUS
contratos con el transportista (la app es multi-cliente: nunca una cuenta
nuestra por defecto), así que todo cuelga de ``CarrierAccount.owner``.

- ``CarrierAccount``: la cuenta del cliente en el transportista — credenciales
  (cifradas, como los tokens de las tiendas), contratos, remitente y domicilio
  de origen, y el paquete por defecto.
- ``CarrierShipment``: un envío creado en el transportista para un pedido, con
  su número de seguimiento (el que va debajo del código de barras del rótulo).
- ``CarrierShipmentEvent``: los movimientos que informa el transportista.

Lo propio de cada transportista vive en su carpeta (``andreani/``,
``correo_argentino/``, ``oca/``) y se usa a través de su ``CarrierProvider``
(``base.py``, ``providers.py``): qué datos pide la cuenta, cómo se cotiza, se
crea, se imprime, se sigue y se cancela un envío.
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.integrations import crypto


class CarrierAccount(models.Model):
    class Carrier(models.TextChoices):
        ANDREANI = "andreani", "Andreani"
        CORREO_ARGENTINO = "correo_argentino", "Correo Argentino"
        OCA = "oca", "OCA"

    class Environment(models.TextChoices):
        PRODUCTION = "production", "Producción"
        QA = "qa", "Pruebas (QA)"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="carrier_accounts")
    carrier = models.CharField(max_length=30, choices=Carrier.choices)
    environment = models.CharField(max_length=20, choices=Environment.choices, default=Environment.PRODUCTION)
    username = models.CharField(max_length=150, blank=True, default="")
    # Cifradas con apps.integrations.crypto: leer/escribir vía ``password``/``token``.
    password_encrypted = models.TextField(blank=True, default="")
    # Código de cliente que asigna el transportista (Andreani lo pide para cotizar).
    client_code = models.CharField(max_length=50, blank=True, default="")
    # Contratos del cliente con el transportista: uno por servicio. Lista de
    # ``{"code", "label", "kind"}``, con ``kind`` "home" (entrega a domicilio)
    # o "branch" (entrega en sucursal o punto HOP).
    contracts = models.JSONField(default=list, blank=True)
    # Remitente que figura en el envío (y desde dónde sale).
    sender_name = models.CharField(max_length=150, blank=True, default="")
    sender_email = models.EmailField(blank=True, default="")
    sender_phone = models.CharField(max_length=30, blank=True, default="")
    sender_document = models.CharField(max_length=20, blank=True, default="")
    origin_street = models.CharField(max_length=120, blank=True, default="")
    origin_number = models.CharField(max_length=20, blank=True, default="")
    origin_floor = models.CharField(max_length=20, blank=True, default="")
    origin_apartment = models.CharField(max_length=20, blank=True, default="")
    origin_postal_code = models.CharField(max_length=10, blank=True, default="")
    origin_city = models.CharField(max_length=120, blank=True, default="")
    # Provincia del origen, como la escribe el cliente (Correo Argentino y OCA
    # la piden; ``common.province_code`` la lleva a su código).
    origin_state = models.CharField(max_length=100, blank=True, default="")
    # El paquete que se informa cuando el pedido no dice cuánto pesa. Andreani
    # pide el volumen; Correo Argentino y OCA, las medidas de la caja.
    default_weight_kg = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal("1"))
    default_volume_cm3 = models.PositiveIntegerField(default=4000)
    default_length_cm = models.PositiveSmallIntegerField(default=20)
    default_width_cm = models.PositiveSmallIntegerField(default=20)
    default_height_cm = models.PositiveSmallIntegerField(default=10)
    # Datos propios de cada transportista que no son secretos (el número de
    # cuenta de OCA, el customerId de MiCorreo...): las claves las declara su
    # ``CarrierProvider.account_fields``.
    extra = models.JSONField(default=dict, blank=True)
    # Secretos además de ``password`` (la clave de MiCorreo de Correo
    # Argentino): un JSON cifrado, leer/escribir vía ``secret()``/``set_secret()``.
    secrets_encrypted = models.TextField(blank=True, default="")
    # Token de sesión del transportista (Andreani: dura 24 horas).
    token_encrypted = models.TextField(blank=True, default="")
    token_expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "cuenta de transportista"
        verbose_name_plural = "cuentas de transportista"
        constraints = [models.UniqueConstraint(fields=["owner", "carrier"], name="unique_carrier_account_per_owner")]

    def __str__(self):
        return f"{self.get_carrier_display()} de {self.owner}"

    @property
    def password(self):
        return crypto.decrypt(self.password_encrypted)

    @password.setter
    def password(self, value):
        self.password_encrypted = crypto.encrypt(value)

    @property
    def token(self):
        return crypto.decrypt(self.token_encrypted)

    @token.setter
    def token(self, value):
        self.token_encrypted = crypto.encrypt(value)

    def _secrets(self):
        raw = crypto.decrypt(self.secrets_encrypted) if self.secrets_encrypted else ""
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def secret(self, name):
        return str(self._secrets().get(name) or "")

    def has_secret(self, name):
        return bool(self.secret(name))

    def set_secret(self, name, value):
        data = self._secrets()
        if value:
            data[name] = str(value)
        else:
            data.pop(name, None)
        self.secrets_encrypted = crypto.encrypt(json.dumps(data)) if data else ""

    def contract(self, code):
        """El contrato ``code`` de la cuenta, o ``None``."""
        return next((item for item in self.contracts or [] if str(item.get("code")) == str(code)), None)


class CarrierShipment(models.Model):
    class DeliveryKind(models.TextChoices):
        HOME = "home", "A domicilio"
        BRANCH = "branch", "En sucursal o punto HOP"

    class Status(models.TextChoices):
        PENDING = "pending", "Creado, falta entregarlo al correo"
        IN_TRANSIT = "in_transit", "En camino"
        AT_BRANCH = "at_branch", "Esperando retiro en sucursal"
        DELIVERED = "delivered", "Entregado"
        ISSUE = "issue", "Con problema"
        RETURNING = "returning", "En devolución"
        CANCELLED = "cancelled", "Cancelado"

    # Estados en los que el transportista ya no tiene nada más que informar.
    FINAL_STATUSES = (Status.DELIVERED, Status.CANCELLED)

    account = models.ForeignKey(CarrierAccount, on_delete=models.PROTECT, related_name="shipments")
    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="carrier_shipments")
    carrier = models.CharField(max_length=30, choices=CarrierAccount.Carrier.choices)
    contract = models.CharField(max_length=50)
    delivery_kind = models.CharField(max_length=20, choices=DeliveryKind.choices, default=DeliveryKind.HOME)
    branch_id = models.CharField(max_length=30, blank=True, default="")
    branch_name = models.CharField(max_length=150, blank=True, default="")
    # El número de envío del transportista: es el seguimiento del pedido y el
    # código que imprime el rótulo debajo del código de barras.
    tracking_number = models.CharField(max_length=50)
    # Andreani: agrupa los bultos de una orden (sus etiquetas salen juntas).
    group_number = models.CharField(max_length=50, blank=True, default="")
    package_count = models.PositiveSmallIntegerField(default=1)
    # Lo que cotizó el transportista al crearlo, con IVA (null si no se pudo).
    quoted_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # Lo último que dijo el transportista, en sus palabras.
    carrier_status = models.CharField(max_length=120, blank=True, default="")
    last_event_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    # Respuesta del alta (sucursales asignadas, links de etiqueta): no trae
    # datos del comprador.
    raw_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "envío"
        verbose_name_plural = "envíos"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["carrier", "tracking_number"], name="unique_carrier_tracking_number")
        ]
        indexes = [models.Index(fields=["status", "last_checked_at"])]

    def __str__(self):
        return f"{self.get_carrier_display()} {self.tracking_number}"

    @property
    def is_final(self):
        return self.status in self.FINAL_STATUSES


class CarrierPushToken(models.Model):
    """Credenciales con las que un transportista nos avisa las novedades de los
    envíos de UNA cuenta (Andreani "Novedades Push": Basic auth contra
    ``/api/v1/carriers/<carrier>/push/<key>/``). La contraseña se guarda solo
    como hash; se muestra una vez, al generarla."""

    account = models.OneToOneField(CarrierAccount, on_delete=models.CASCADE, related_name="push_token")
    key = models.CharField(max_length=40, unique=True)
    username = models.CharField(max_length=40)
    password_hash = models.CharField(max_length=128)
    last_received_at = models.DateTimeField(null=True, blank=True)
    received_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "credencial de novedades push"
        verbose_name_plural = "credenciales de novedades push"

    def __str__(self):
        return f"Push de {self.account}"


class CarrierShipmentEvent(models.Model):
    """Un movimiento del envío, como lo informa el transportista."""

    shipment = models.ForeignKey(CarrierShipment, on_delete=models.CASCADE, related_name="events")
    occurred_at = models.DateTimeField()
    cycle = models.CharField(max_length=50, blank=True, default="")
    event = models.CharField(max_length=80, blank=True, default="")
    reason = models.CharField(max_length=150, blank=True, default="")
    sub_reason = models.CharField(max_length=150, blank=True, default="")
    status_text = models.CharField(max_length=150, blank=True, default="")
    branch = models.CharField(max_length=150, blank=True, default="")
    comment = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        verbose_name = "movimiento de envío"
        verbose_name_plural = "movimientos de envío"
        ordering = ["occurred_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["shipment", "occurred_at", "event"], name="unique_shipment_event")
        ]

    def __str__(self):
        return f"{self.shipment} {self.occurred_at:%Y-%m-%d %H:%M} {self.event}"


class ManualCarrier(models.Model):
    """Catálogo GLOBAL (lo administra el admin, ``catalogs.manage``) de
    transportistas sin API: los que se despachan cargando el seguimiento a
    mano (Via Cargo, un expreso, una moto). Aparecen como sugerencia donde se
    escribe el transportista y, si tienen ``tracking_url_template``, arman el
    link de seguimiento solos. No reemplaza a los transportistas con API
    (``providers.py``): esos se eligen por su código."""

    name = models.CharField(max_length=100, unique=True)
    # Con {tracking} donde va el número: "https://ejemplo.com/seguimiento?n={tracking}".
    tracking_url_template = models.CharField(max_length=500, blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "transportista manual"
        verbose_name_plural = "transportistas manuales"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def tracking_url_for(self, tracking_number):
        if not self.tracking_url_template or not tracking_number:
            return ""
        from urllib.parse import quote

        return self.tracking_url_template.replace("{tracking}", quote(str(tracking_number), safe=""))
