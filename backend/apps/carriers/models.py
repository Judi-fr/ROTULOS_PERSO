"""Transportistas (correos) con los que despachan los clientes: hoy Andreani.

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

Lo propio de cada transportista vive en su carpeta (``andreani/``).
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.integrations import crypto


class CarrierAccount(models.Model):
    class Carrier(models.TextChoices):
        ANDREANI = "andreani", "Andreani"

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
    # El paquete que se informa cuando el pedido no dice cuánto pesa.
    default_weight_kg = models.DecimalField(max_digits=8, decimal_places=3, default=Decimal("1"))
    default_volume_cm3 = models.PositiveIntegerField(default=4000)
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
