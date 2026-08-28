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


class LoginLockout(models.Model):
    """Bloqueo temporal de login por intentos fallidos consecutivos.

    Igual patrón que :class:`EmailVerification`: una tabla satélite en vez de
    un campo en ``User``, porque el proyecto usa el modelo ``User`` default de
    Django (no hay ``AUTH_USER_MODEL`` propio).

    No hay job/cron para "desbloquear": la expiración se resuelve al vuelo,
    la primera vez que algo pregunta ``is_locked()`` (típicamente el próximo
    intento de login) comparando contra ``locked_until``.
    """

    THRESHOLD = 3
    DURATION = timedelta(hours=1)

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="login_lockout")
    failed_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "bloqueo de login"
        verbose_name_plural = "bloqueos de login"

    def is_locked(self):
        """¿Sigue bloqueado ahora mismo?

        Si ``locked_until`` ya pasó, el bloqueo se limpia acá mismo (contador
        incluido, para que el próximo ciclo de intentos arranque de cero) y
        se devuelve ``False``. Es la "expiración automática" sin cron.
        """
        if self.locked_until is None:
            return False
        if timezone.now() >= self.locked_until:
            self.failed_attempts = 0
            self.locked_until = None
            self.save(update_fields=["failed_attempts", "locked_until"])
            return False
        return True

    def remaining_seconds(self):
        if not self.is_locked():
            return 0
        return max(0, int((self.locked_until - timezone.now()).total_seconds()))

    def register_failure(self):
        """Suma un intento fallido; al llegar al umbral, bloquea 1 hora."""
        self.failed_attempts += 1
        if self.failed_attempts >= self.THRESHOLD:
            self.locked_until = timezone.now() + self.DURATION
        self.save(update_fields=["failed_attempts", "locked_until"])

    def register_success(self):
        """Login exitoso: resetea el contador de intentos fallidos."""
        if self.failed_attempts or self.locked_until:
            self.failed_attempts = 0
            self.locked_until = None
            self.save(update_fields=["failed_attempts", "locked_until"])

    def unlock(self):
        """Desbloqueo manual (admin): resetea contador y ``locked_until``."""
        self.failed_attempts = 0
        self.locked_until = None
        self.save(update_fields=["failed_attempts", "locked_until"])

    def __str__(self):
        estado = "bloqueado" if self.is_locked() else "libre"
        return f"{self.user.email} ({estado})"


class PasswordChangeRequirement(models.Model):
    """Obliga a un usuario a cambiar su contraseña antes de seguir usando la API.

    Mismo patrón satélite que :class:`LoginLockout`: el proyecto usa el
    ``User`` default de Django (sin ``AUTH_USER_MODEL`` propio), así que este
    flag no puede vivir como campo directo del modelo User.

    Se activa en dos casos (ver ``UserAdminSerializer``):
    - Automáticamente, cuando un admin crea un usuario sin indicar
      contraseña (se usa ``settings.ADMIN_CREATED_USER_PASSWORD`` como
      temporal).
    - Manualmente, cuando un admin lo tilda sobre un usuario existente.

    Se desactiva al completar un cambio de contraseña exitoso vía
    ``ChangePasswordView`` (``/api/v1/auth/me/change-password/``).

    No se crea una fila para cada usuario (a diferencia de LoginLockout, que
    se crea perezosamente en el primer intento de login): solo existe fila
    cuando el flag estuvo o está en True, para no escribir en la base por
    cada alta de usuario.
    """

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="password_change_requirement"
    )
    must_change_password = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "requisito de cambio de contraseña"
        verbose_name_plural = "requisitos de cambio de contraseña"

    def __str__(self):
        estado = "pendiente" if self.must_change_password else "cumplido"
        return f"{self.user.email} ({estado})"


class SupportMessage(models.Model):
    """Mensaje de contacto enviado desde el panel del usuario ("Ayuda/Soporte").

    Sin integración de email real todavía (ver dashboard.html): el mensaje
    solo se persiste acá y lo lee un admin desde /admin/. Cuando exista el
    canal real (email/ticketing), este modelo es el punto de partida.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="support_messages")
    subject = models.CharField(max_length=150)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "mensaje de soporte"
        verbose_name_plural = "mensajes de soporte"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.email}: {self.subject}"


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
