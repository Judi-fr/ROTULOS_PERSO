"""Mapa de permisos basado en roles (ADITIVO).

Esta capa es exclusivamente **aditiva**: NO reemplaza ni modifica la lógica
de permisos que ya usan las vistas actuales (``IsAdminUser`` en
``UserAdminViewSet``, ``IsAuthenticated`` en ``MeView``). Su propósito es
consolidar, en un único sitio, los permisos atómicos y el mapeo
rol -> permisos, reproduciendo **exactamente** la lógica de determinación
de roles ya usada por el backend (ver ``apps.accounts.serializers.get_user_role``).

Detalles de la auditoría (ver Parte A) que justifican esta implementación:

- El modelo ``User`` del proyecto **no posee un campo ``role``**. El rol se
  determina exclusivamente por **Django Groups** (nombrados como el rol) y
  el fallback ``is_staff``.
- Rol determinado por ``IsAdminUser`` = ``request.user.is_staff``.
- Roles válidos del sistema: admin, designer, operator, subscriber.
- ``DEFAULT_ROLE`` (fallback) = subscriber.
- El rol legado ``"user"`` se interpreta como ``subscriber``.

Este módulo aún **no se aplica a ningún endpoint**: existe para ser consultado
por componentes futuros (control de UI, filters, etc.) sin alterar el
comportamiento actual de las APIs.
"""

from django.contrib.auth import get_user_model

try:
    from .models import GroupRolePermission, RolePermission
except Exception:  # pragma: no cover - import temprano durante migraciones
    GroupRolePermission = None
    RolePermission = None

User = get_user_model()

# Roles válidos del sistema (coinciden con ROLE_CHOICES del backend).
VALID_ROLES = ("admin", "designer", "operator", "subscriber")
DEFAULT_ROLE = "subscriber"

# Alias legacy que deben mapearse a un rol válido.
# "user" era el rol genérico antiguo y ahora equivale a subscriber.
LEGACY_ROLE_ALIASES = {
    "user": DEFAULT_ROLE,
    "administrador": "admin",
    "administradores": "admin",
    "administrator": "admin",
    "diseñador": "designer",
    "diseñadores": "designer",
    "disenador": "designer",
    "disenadores": "designer",
    "operador": "operator",
    "operadores": "operator",
}


def normalize_role(role):
    """Normaliza un rol recibido como entrada.

    - Si es ``None``, devuelve ``DEFAULT_ROLE``.
    - Si es un alias legado (``"user"``, ``"administrador"``, ...), lo mapea
      a su rol canónico equivalente.
    - Si es un rol válido, lo devuelve tal cual (minúscula).
    - Si es un rol PERSONALIZADO (no reconocido), lo devuelve tal cual
      (minúscula, sin espacios) en lugar de colapsarlo a ``DEFAULT_ROLE``.
      Colapsarlo rompería la asignación de permisos de roles personalizados:
      ``get_effective_role`` dejaría de ver el Group real y
      ``user_has_permission`` consultaría ``GroupRolePermission`` para
      "subscriber" en vez del rol efectivamente asignado al usuario.
    """
    if role is None:
        return DEFAULT_ROLE
    role = str(role).strip().lower()
    if role in LEGACY_ROLE_ALIASES:
        return LEGACY_ROLE_ALIASES[role]
    return role


# Conjunto de permisos atómicos del sistema.
PERMISSIONS = frozenset(
    {
        # Administración de usuarios (CRUD admin)
        "users.view",
        "users.create",
        "users.edit",
        "users.deactivate",
        "users.reactivate",
        "users.unlock",
        # Perfil propio
        "users.me.view",
        "users.me.edit",
        "users.me.change_password",
        # Direcciones y pedidos propios (cliente final)
        "addresses.manage",
        "orders.view",
        "orders.create",
        "orders.cancel",
        # Contacto/soporte desde el dashboard
        "support.create",
    }
)

# Permisos de self-service: iguales para los tres roles no-admin (designer,
# operator, subscriber). "subscriber" es hoy el rol de cliente final.
_SELF_SERVICE_PERMISSIONS = {
    "users.me.view",
    "users.me.edit",
    "users.me.change_password",
    "addresses.manage",
    "orders.view",
    "orders.create",
    "orders.cancel",
    "support.create",
}

# Mapeo rol -> conjunto de permisos.
# Reproduce EXACTAMENTE el comportamiento actual del backend:
#   - admin: acceso completo (el CRUD admin está protegido por IsAdminUser).
#   - designer / operator / subscriber: acceso a su propio perfil, sus
#     direcciones y sus pedidos.
ROLE_PERMISSIONS = {
    "admin": set(PERMISSIONS),
    "designer": set(_SELF_SERVICE_PERMISSIONS),
    "operator": set(_SELF_SERVICE_PERMISSIONS),
    "subscriber": set(_SELF_SERVICE_PERMISSIONS),
}


def get_effective_role(user):
    """Determina el rol efectivo del usuario.

    Reproduce la MISMA lógica que ``apps.accounts.serializers.get_user_role``:

    1. Si el usuario pertenece a un Group cuyo nombre coincide con un rol
       válido (recorriendo el orden de ``VALID_ROLES``), devuelve ese rol.
    2. Si el usuario pertenece a un Group personalizado (no canónico),
       devuelve el nombre de ese Group (rol personalizado).
    3. Si el usuario es ``is_staff`` (sin group explícito), devuelve
       ``admin`` (compatibilidad con usuarios staff creados sin group).
    4. En caso contrario, devuelve ``DEFAULT_ROLE`` (``subscriber``).

    El rol legado ``"user"`` no es un valor posible de retorno de esta
    función (nunca se devuelve ``"user"``); si un rol de entrada fuera
    ``"user"``, ``normalize_role`` lo mapea a ``subscriber``.
    """
    if user is None or not getattr(user, "is_authenticated", True):
        return DEFAULT_ROLE

    # 1. Los Groups legacy se normalizan al rol canónico antes de evaluar.
    # Esto conserva a Groups como origen del rol, sin exigir que usuarios
    # históricos se reasignen manualmente antes de usar permisos configurables.
    user_groups = {
        normalize_role(group_name)
        for group_name in user.groups.values_list("name", flat=True)
    }
    for role in VALID_ROLES:
        if role in user_groups:
            return role

    # 2. Rol personalizado: devolver el nombre del primer Group no canónico.
    for group_name in user.groups.values_list("name", flat=True):
        normalized = normalize_role(group_name)
        if normalized not in VALID_ROLES:
            return normalized

    # 3. Compatibilidad: staff sin group -> admin.
    if getattr(user, "is_staff", False):
        return "admin"

    # 4. Fallback.
    return DEFAULT_ROLE


def user_has_permission(user, permission):
    """Indica si el usuario efectivamente posee el permiso atómico indicado.

    - Devuelve ``False`` para usuarios anónimos / no autenticados.
    - Devuelve ``False`` si el permiso no está en el catálogo base
      (``PERMISSIONS``, catálogo de compatibilidad temporal).
    - Si la tabla ``GroupRolePermission`` está disponible, consulta la base
      de datos (fuente de verdad). Si todavía no existe (p. ej. durante
      migraciones tempranas), usa el mapa estático ``ROLE_PERMISSIONS`` como
      fallback para no romper el comportamiento actual.
    """
    if not permission or permission not in PERMISSIONS:
        return False
    if not getattr(user, "is_authenticated", False):
        return False

    # Fuente de verdad: base de datos (si el modelo ya existe).
    if GroupRolePermission is not None:
        # Consultar el Group del rol efectivo mantiene a GroupRolePermission
        # como fuente de verdad. También cubre usuarios legacy sin Group: su
        # rol efectivo es admin (si son staff) o subscriber (fallback).
        role = get_effective_role(user)
        return GroupRolePermission.objects.filter(
            group__name=role,
            permission__key=permission,
        ).exists()

    # Fallback pre-migración: mapa estático (comportamiento anterior).
    role = get_effective_role(user)
    return permission in ROLE_PERMISSIONS.get(role, set())
