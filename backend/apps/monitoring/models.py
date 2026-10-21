from django.conf import settings
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


class MerchantNotice(models.Model):
    """Un aviso para un comercio: algo de SU comercio que se rompió y no se
    arregla solo (``notices.py``). Queda abierto mientras dure el problema;
    al resolverse se cierra (``resolved_at``) y, si vuelve, es uno nuevo."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="merchant_notices")
    key = models.CharField(max_length=80)
    kind = models.CharField(max_length=30)
    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True, default="")
    url = models.CharField(max_length=200, blank=True, default="")
    # El acceso del equipo que hace falta para verlo (teams.ACCESSES) y las
    # tiendas a las que se refiere (null = a ninguna en particular).
    access = models.CharField(max_length=30)
    store_ids = models.JSONField(null=True, blank=True, default=None)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    mailed_at = models.DateTimeField(null=True, blank=True)
    mail_attempted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "aviso al comercio"
        verbose_name_plural = "avisos a los comercios"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "key"], condition=models.Q(resolved_at=None), name="merchant_notice_open_unique"
            )
        ]

    def __str__(self):
        return f"{self.owner} · {self.title}"
