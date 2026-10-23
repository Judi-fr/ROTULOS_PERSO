"""Comercios con equipo: dueño y empleados (ver ``teams.py`` y
``docs/superpowers/specs/2026-10-19-equipos-design.md``).

Los datos del comercio (tiendas, pedidos, plantillas...) cuelgan de su
USUARIO DE DATOS (``Team.data_user``), un usuario interno sin login; estos
modelos dicen quién trabaja en qué comercio y con qué accesos. Una persona
puede estar en varios comercios. Se importan desde ``models.py``.
"""

from django.conf import settings
from django.db import models


class Team(models.Model):
    """Un comercio. Una persona puede estar en varios (``teams.membership``
    dice cuál es el de la request)."""

    # La PERSONA dueña. Solo queda vacío en un comercio dado de baja cuyo
    # dueño se borró (el borrado permanente se ocupa de los abiertos).
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="owned_teams"
    )
    # Del que cuelgan los datos del comercio: inactivo, sin contraseña, con un
    # mail que no existe (``teams.create_team``). No es una persona.
    data_user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="data_team"
    )
    name = models.CharField("nombre", max_length=150, blank=True, default="")
    # Código fijo para pedir sumarse (queda pendiente hasta que lo aprueben).
    join_code = models.CharField("código para sumarse", max_length=20, unique=True)
    join_code_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Baja (``team_services.close_team``): los datos quedan en la base,
    # colgando del usuario de datos; nadie es miembro hasta que el admin lo
    # reabre.
    closed_at = models.DateTimeField("dado de baja", null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    close_reason = models.TextField("motivo de la baja", blank=True, default="")
    # Lo que la baja apagó (ids por tipo), para volver a prenderlo al reabrir.
    closed_state = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "comercio"
        verbose_name_plural = "comercios"

    def __str__(self):
        return self.name or f"Comercio de {self.owner or '(sin dueño)'}"

    @property
    def is_closed(self):
        return self.closed_at is not None


class TeamProfile(models.Model):
    """Conjunto de accesos con nombre ("Depósito"), propio de cada comercio."""

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="profiles")
    name = models.CharField("nombre", max_length=60)
    # Claves de teams.ACCESSES.
    accesses = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "perfil del comercio"
        verbose_name_plural = "perfiles del comercio"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["team", "name"], name="team_profile_unique_name")]

    def __str__(self):
        return self.name


class TeamMember(models.Model):
    """Quién trabaja en el comercio. Una persona puede estar en varios, con
    un rol distinto en cada uno."""

    class Role(models.TextChoices):
        OWNER = "owner", "Dueño"
        EMPLOYEE = "employee", "Empleado"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_memberships")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)
    # Con perfil, sus accesos son los del perfil; sin perfil ("personalizado"),
    # los de ``accesses``. El dueño tiene siempre todos.
    profile = models.ForeignKey(TeamProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name="members")
    accesses = models.JSONField(default=list, blank=True)
    all_stores = models.BooleanField(default=True)
    stores = models.ManyToManyField("integrations.StoreConnection", blank=True, related_name="+")
    joined_at = models.DateTimeField(auto_now_add=True)
    # El comercio en el que se entra sin elegir (login, request sin header).
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "miembro del comercio"
        verbose_name_plural = "miembros del comercio"
        constraints = [
            models.UniqueConstraint(fields=["team", "user"], name="team_member_unique_user"),
            models.UniqueConstraint(
                fields=["team"], condition=models.Q(role="owner"), name="team_member_one_owner"
            ),
        ]

    def __str__(self):
        return f"{self.user} ({self.get_role_display()}) en {self.team}"


class TeamInvitation(models.Model):
    """Invitación por mail. La persona la tiene que ACEPTAR (decidido con el
    usuario): nadie entra a un comercio sin confirmarlo."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        ACCEPTED = "accepted", "Aceptada"
        DECLINED = "declined", "Rechazada"
        CANCELLED = "cancelled", "Cancelada"
        EXPIRED = "expired", "Vencida"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    profile = models.ForeignKey(TeamProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    accesses = models.JSONField(default=list, blank=True)
    all_stores = models.BooleanField(default=True)
    stores = models.ManyToManyField("integrations.StoreConnection", blank=True, related_name="+")
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    token = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "invitación al comercio"
        verbose_name_plural = "invitaciones al comercio"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email} → {self.team} ({self.status})"


class TeamJoinRequest(models.Model):
    """Pedido para sumarse con el código del comercio: queda pendiente hasta
    que alguien con el acceso "Equipo" lo aprueba (eligiéndole perfil y
    tiendas) o lo rechaza."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        APPROVED = "approved", "Aprobado"
        REJECTED = "rejected", "Rechazado"
        CANCELLED = "cancelled", "Cancelado"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="join_requests")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_join_requests")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "pedido para sumarse al comercio"
        verbose_name_plural = "pedidos para sumarse al comercio"
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["team", "user"], condition=models.Q(status="pending"), name="team_join_request_one_pending"
            ),
        ]

    def __str__(self):
        return f"{self.user} → {self.team} ({self.status})"
