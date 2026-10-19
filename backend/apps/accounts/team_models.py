"""Comercios con equipo: dueño y empleados (ver ``teams.py`` y
``docs/superpowers/specs/2026-10-19-equipos-design.md``).

Los datos del comercio (tiendas, pedidos, plantillas...) siguen colgando del
usuario DUEÑO; estos modelos solo dicen quién trabaja en qué comercio y con
qué accesos. Se importan desde ``models.py``.
"""

from django.conf import settings
from django.db import models


class Team(models.Model):
    """Un comercio. Todo usuario pertenece a uno (``teams.team_for``)."""

    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_team")
    name = models.CharField("nombre", max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "comercio"
        verbose_name_plural = "comercios"

    def __str__(self):
        return self.name or f"Comercio de {self.owner}"


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
    """Quién trabaja en el comercio. Un usuario, un solo comercio."""

    class Role(models.TextChoices):
        OWNER = "owner", "Dueño"
        EMPLOYEE = "employee", "Empleado"

    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="team_membership")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)
    # Con perfil, sus accesos son los del perfil; sin perfil ("personalizado"),
    # los de ``accesses``. El dueño tiene siempre todos.
    profile = models.ForeignKey(TeamProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name="members")
    accesses = models.JSONField(default=list, blank=True)
    all_stores = models.BooleanField(default=True)
    stores = models.ManyToManyField("integrations.StoreConnection", blank=True, related_name="+")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "miembro del comercio"
        verbose_name_plural = "miembros del comercio"

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
