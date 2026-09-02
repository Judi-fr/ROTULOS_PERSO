"""Registro de auditoría.

``AuditLog`` es el único modelo de esta app: cada fila es un evento ya
ocurrido (login, alta de usuario, cambio de rol, respuesta de soporte, ...).
Es deliberadamente un modelo "plano" (sin ``GenericForeignKey``/
``contenttypes``): ``target_type``/``target_id``/``target_repr`` alcanzan
para leer el registro y son mucho más simples de consultar y testear.

Es inmutable: ``save()`` rechaza cualquier intento de modificar una fila ya
creada y ``delete()`` rechaza el borrado. No hay ningún endpoint de
escritura/borrado sobre este modelo (ver ``views.py``): la única forma de
crear una fila es ``apps.audit.services.record``.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    class Category(models.TextChoices):
        AUTH = "auth", "Autenticación"
        USERS = "users", "Usuarios"
        ROLES = "roles", "Roles"
        ORDERS = "orders", "Pedidos"
        SUPPORT = "support", "Soporte"

    class Action(models.TextChoices):
        # auth
        AUTH_LOGIN_SUCCESS = "auth.login_success", "Login exitoso"
        AUTH_LOGIN_FAILED = "auth.login_failed", "Login fallido"
        AUTH_LOCKOUT = "auth.lockout", "Bloqueo de cuenta"
        AUTH_LOGOUT = "auth.logout", "Logout"
        AUTH_PASSWORD_CHANGE = "auth.password_change", "Cambio de contraseña"
        AUTH_PASSWORD_RESET = "auth.password_reset", "Restablecimiento de contraseña"
        # users
        USER_CREATE = "user.create", "Usuario creado"
        USER_UPDATE = "user.update", "Usuario actualizado"
        USER_DEACTIVATE = "user.deactivate", "Usuario desactivado"
        USER_REACTIVATE = "user.reactivate", "Usuario reactivado"
        USER_UNLOCK = "user.unlock", "Usuario desbloqueado"
        USER_ROLE_CHANGE = "user.role_change", "Cambio de rol de usuario"
        # roles
        ROLE_CREATE = "role.create", "Rol creado"
        ROLE_UPDATE = "role.update", "Rol actualizado"
        ROLE_DELETE = "role.delete", "Rol eliminado"
        ROLE_PERMISSIONS_UPDATE = "role.permissions_update", "Permisos de rol actualizados"
        # orders
        ORDER_CREATE = "order.create", "Pedido creado"
        ORDER_STATUS_CHANGE = "order.status_change", "Cambio de estado de pedido"
        ORDER_CANCEL = "order.cancel", "Pedido cancelado"
        # support
        SUPPORT_CREATE = "support.create", "Mensaje de soporte creado"
        SUPPORT_STATUS_CHANGE = "support.status_change", "Cambio de estado de soporte"
        SUPPORT_REPLY = "support.reply", "Respuesta de soporte"

    # Cada acción pertenece a exactamente una categoría. Se guarda acá (en vez
    # de derivarla del prefijo de la acción) porque los nombres no son
    # simétricos: la categoría "users" agrupa acciones "user.*" (singular),
    # "roles" agrupa "role.*", "orders" agrupa "order.*".
    ACTION_CATEGORIES = {
        Action.AUTH_LOGIN_SUCCESS: Category.AUTH,
        Action.AUTH_LOGIN_FAILED: Category.AUTH,
        Action.AUTH_LOCKOUT: Category.AUTH,
        Action.AUTH_LOGOUT: Category.AUTH,
        Action.AUTH_PASSWORD_CHANGE: Category.AUTH,
        Action.AUTH_PASSWORD_RESET: Category.AUTH,
        Action.USER_CREATE: Category.USERS,
        Action.USER_UPDATE: Category.USERS,
        Action.USER_DEACTIVATE: Category.USERS,
        Action.USER_REACTIVATE: Category.USERS,
        Action.USER_UNLOCK: Category.USERS,
        Action.USER_ROLE_CHANGE: Category.USERS,
        Action.ROLE_CREATE: Category.ROLES,
        Action.ROLE_UPDATE: Category.ROLES,
        Action.ROLE_DELETE: Category.ROLES,
        Action.ROLE_PERMISSIONS_UPDATE: Category.ROLES,
        Action.ORDER_CREATE: Category.ORDERS,
        Action.ORDER_STATUS_CHANGE: Category.ORDERS,
        Action.ORDER_CANCEL: Category.ORDERS,
        Action.SUPPORT_CREATE: Category.SUPPORT,
        Action.SUPPORT_STATUS_CHANGE: Category.SUPPORT,
        Action.SUPPORT_REPLY: Category.SUPPORT,
    }

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    # Snapshot del email al momento del evento: el registro sigue siendo
    # legible aunque la cuenta cambie de email o el actor se borre (actor
    # queda null por el SET_NULL). En eventos sin actor autenticado (login
    # fallido) guarda el email intentado.
    actor_email = models.CharField(max_length=254, blank=True, default="")

    category = models.CharField(max_length=20, choices=Category.choices)
    action = models.CharField(max_length=40, choices=Action.choices)

    target_type = models.CharField(max_length=50, blank=True, default="")
    # CharField (no IntegerField): los ids de Group (roles) también entran acá.
    target_id = models.CharField(max_length=64, blank=True, default="")
    target_repr = models.CharField(max_length=255, blank=True, default="")

    # Diff {"campo": {"from": ..., "to": ...}}. Nunca contraseñas/hashes/
    # tokens: si el campo es sensible se registra el nombre con "***".
    changes = models.JSONField(default=dict, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "registro de auditoría"
        verbose_name_plural = "registros de auditoría"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["-created_at"]),
            models.Index(fields=["category"]),
            models.Index(fields=["actor"]),
        ]

    def __str__(self):
        return f"{self.action} ({self.actor_email or 'sistema'})"

    def save(self, *args, **kwargs):
        # Inmutable: solo se permite el INSERT inicial (pk todavía None).
        if self.pk is not None:
            raise ValueError(
                "AuditLog es inmutable: no se puede modificar un registro existente."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog es inmutable: no se puede eliminar un registro.")
