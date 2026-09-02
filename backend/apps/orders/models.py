"""Direcciones y pedidos del usuario final (cliente).

El seguimiento en tiempo real con el courier todavía no está integrado:
no tenemos la API de la empresa de logística. Los campos de esa
integración (``carrier``, ``tracking_number``, ``tracking_url``) quedan
opcionales a la espera de esa conexión; mientras tanto el estado del
pedido se actualiza a mano (p. ej. desde el admin de Django) recorriendo
las etapas de ``Order.Status``. Cuando la API esté disponible, el punto
de integración es justamente completar esos tres campos.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class Address(models.Model):
    """Dirección de envío guardada por un usuario para sus pedidos."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="addresses",
    )
    label = models.CharField(max_length=50, blank=True, default="")
    street = models.CharField(max_length=255)
    number = models.CharField(max_length=20, blank=True, default="")
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100, blank=True, default="")
    postal_code = models.CharField(max_length=20, blank=True, default="")
    country = models.CharField(max_length=100, default="Argentina")
    # Ej: "timbre azul", "portón negro" — ayuda al repartidor a encontrar el lugar.
    reference = models.CharField(max_length=255, blank=True, default="")
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "dirección"
        verbose_name_plural = "direcciones"
        ordering = ["-is_default", "-created_at"]

    def __str__(self):
        return f"{self.label or self.street} ({self.user.email})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Solo puede haber UNA dirección predeterminada por usuario: al marcar
        # esta como default, se desmarcan todas las demás del mismo usuario.
        if self.is_default:
            Address.objects.filter(user_id=self.user_id).exclude(pk=self.pk).update(
                is_default=False
            )


class Order(models.Model):
    """Pedido/envío de un usuario sobre una de sus direcciones guardadas."""

    class Status(models.TextChoices):
        CREATED = "created", "Creado"
        PREPARING = "preparing", "En preparación"
        DISPATCHED = "dispatched", "Despachado"
        IN_TRANSIT = "in_transit", "En tránsito"
        DELIVERED = "delivered", "Entregado"
        CANCELLED = "cancelled", "Cancelado"

    # Estados en los que el cliente todavía puede cancelar el pedido por sí
    # mismo (ver OrderViewSet.cancel). Una vez despachado, la cancelación
    # queda fuera de su alcance.
    CANCELLABLE_STATUSES = {Status.CREATED, Status.PREPARING}

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="orders",
    )
    # PROTECT: no se puede borrar una dirección que ya tiene pedidos, para no
    # perder el historial de a dónde se envió cada uno (mismo criterio que el
    # soft-delete de usuarios: no se destruye información con dependencias).
    address = models.ForeignKey(
        Address,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    description = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED)

    # --- Integración con el courier (pendiente hasta tener la API) ---
    carrier = models.CharField(max_length=100, blank=True, default="")
    tracking_number = models.CharField(max_length=100, blank=True, default="")
    tracking_url = models.URLField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "pedido"
        verbose_name_plural = "pedidos"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Pedido #{self.pk or '?'} ({self.user.email})"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._previous_status = self.status

    @property
    def is_cancellable(self):
        return self.status in self.CANCELLABLE_STATUSES

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        # Cada alta o cambio de estado queda registrado en el timeline
        # (OrderStatusEvent), sin importar si lo disparó el cliente
        # (cancelar) o un cambio manual desde el admin.
        if is_new or self.status != self._previous_status:
            self.status_events.create(status=self.status)
            # Auditoría de cambios de estado hechos desde el admin de Django
            # o a mano (sin request, así que sin actor). Las vistas que ya
            # tienen actor (crear, cancelar) registran su propia entrada más
            # específica y marcan ``_skip_status_audit`` para no duplicarla.
            if not is_new and not getattr(self, "_skip_status_audit", False):
                from apps.audit.services import record

                record(
                    None,
                    category="orders",
                    action="order.status_change",
                    target=self,
                    target_type="order",
                    target_repr=str(self),
                    changes={"status": {"from": self._previous_status, "to": self.status}},
                )
        self._previous_status = self.status
        self._skip_status_audit = False


class OrderStatusEvent(models.Model):
    """Registro histórico de cada cambio de estado de un pedido (timeline)."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_events")
    status = models.CharField(max_length=20, choices=Order.Status.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "evento de estado de pedido"
        verbose_name_plural = "eventos de estado de pedido"
        ordering = ["created_at"]

    def __str__(self):
        return f"Pedido #{self.order_id}: {self.status}"
