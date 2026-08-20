from __future__ import annotations

import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import models
from django.utils import timezone

User = get_user_model()


class EmailVerification(models.Model):
    """Estado de verificación de email para un usuario."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="email_verification")
    token = models.CharField(max_length=255, unique=True, blank=True, default="")
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "verificación de email"
        verbose_name_plural = "verificaciones de email"

    def is_expired(self):
        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at

    def refresh_token(self):
        self.token = secrets.token_urlsafe(32)
        self.is_verified = False
        self.verified_at = None
        self.expires_at = timezone.now() + timedelta(days=1)
        self.save(update_fields=["token", "is_verified", "verified_at", "expires_at", "updated_at"])
        return self.token

    def __str__(self):
        state = "verificado" if self.is_verified else "pendiente"
        return f"{self.user.email} ({state})"


class RolePermission(models.Model):
    """Permiso atómico administrable por rol (Django Group).

    La fuente de verdad del ROL sigue siendo el ``Group`` de Django; esta
    tabla solo cataloga los permisos y su asignación a cada Group (rol) vía
    la tabla intermedia :class:`GroupRolePermission`.

    La relación es ManyToMany lógica: cada permiso puede estar asignado a
    varios Groups y cada Group puede tener varios permisos. La combinación
    Group + Permission es única (ver ``GroupRolePermission.UniqueConstraint``).
    """

    key = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=50)

    class Meta:
        verbose_name = "permiso de rol"
        verbose_name_plural = "permisos de rol"
        ordering = ["key"]

    def __str__(self):
        return f"{self.key} ({self.name})"


class GroupRolePermission(models.Model):
    """Asignación de un permiso a un rol (tabla intermedia).

    Evita duplicar el Group: el Group sigue siendo la fuente de verdad del
    rol; acá solo se guarda qué permisos tiene cada rol.
    """

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="role_permission_links",
    )
    permission = models.ForeignKey(
        RolePermission,
        on_delete=models.CASCADE,
        related_name="group_links",
    )

    class Meta:
        verbose_name = "asignación de permiso a rol"
        verbose_name_plural = "asignaciones de permiso a rol"
        constraints = [
            models.UniqueConstraint(
                fields=["group", "permission"],
                name="uniq_group_role_permission",
            ),
        ]

    def __str__(self):
        return f"{self.group.name} -> {self.permission.key}"
