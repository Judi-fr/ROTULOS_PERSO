from django.db import models


class Heartbeat(models.Model):
    """El último "estoy vivo" de un proceso de fondo (``worker``, ``monitor``).

    Va en la base y no en el cache porque el cache por defecto es por proceso:
    la API no vería lo que escribe el worker. ``info`` guarda lo que el
    proceso quiera contar (p. ej. el resultado del último mail de alertas).
    """

    name = models.CharField(max_length=40, unique=True)
    last_seen = models.DateTimeField()
    info = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "latido"
        verbose_name_plural = "latidos"

    def __str__(self):
        return f"{self.name} @ {self.last_seen:%Y-%m-%d %H:%M:%S}"


class AlertState(models.Model):
    """Estado de alerta de un chequeo, para avisar en el CAMBIO y no en cada pasada.

    ``failing_since`` es cuándo empezó a fallar (null = está bien);
    ``notified_at`` cuándo se mandó el último mail por esta falla (null =
    todavía no se avisó: o está en el período de gracia, o el mail falló).
    """

    key = models.CharField(max_length=40, unique=True)
    failing_since = models.DateTimeField(null=True, blank=True)
    notified_at = models.DateTimeField(null=True, blank=True)
    detail = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "estado de alerta"
        verbose_name_plural = "estados de alerta"

    def __str__(self):
        return f"{self.key}: {'falla' if self.failing_since else 'ok'}"
