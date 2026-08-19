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

User = get_user_model()

# Roles válidos del sistema (coinciden con ROLE_CHOICES del backend).
VALID_ROLES = ("admin", "designer", "operator", "subscriber")
DEFAULT_ROLE = "subscriber"

# Alias legacy que deben mapearse a un rol válido.
# "user" era el rol genérico antiguo y ahora equivale a subscriber.
LEGACY_ROLE_ALIASES = {
    "user": DEFAULT_ROLE,
}


def normalize_role(role):
    """Normaliza un rol recibido como entrada.

    - Si es ``None`` o no reconocido, devuelve ``DEFAULT_ROLE``.
    - Si es un alias legado (``"user"``), lo mapea a ``subscriber``.
    - Si es un rol válido, lo devuelve tal cual (minúscula).
    """
    if role is None:
        return DEFAULT_ROLE
    role = str(role).strip().lower()
    if role in LEGACY_ROLE_ALIASES:
        return LEGACY_ROLE_ALIASES[role]
    if role in VALID_ROLES:
        return role
    return DEFAULT_ROLE


# Conjunto de permisos atómicos del sistema.
PERMISSIONS = frozenset(
    {
        # Administración de usuarios (CRUD admin)
        "users.view",
        "users.create",
        "users.edit",
        "users.deactivate",
        "users.reactivate",
        # Perfil propio
        "users.me.view",
        "users.me.edit",
        "users.me.change_password",
    }
)

# Mapeo rol -> conjunto de permisos.
# Reproduce EXACTAMENTE el comportamiento actual del backend:
#   - admin: acceso completo (el CRUD admin está protegido por IsAdminUser).
#   - designer / operator / subscriber: acceso únicamente a su propio perfil.
ROLE_PERMISSIONS = {
    "admin": set(PERMISSIONS),
    "designer": {
        "users.me.view",
        "users.me.edit",
        "users.me.change_password",
    },
    "operator": {
        "users.me.view",
        "users.me.edit",
        "users.me.change_password",
    },
    "subscriber": {
        "users.me.view",
        "users.me.edit",
        "users.me.change_password",
    },
}


def get_effective_role(user):
    """Determina el rol efectivo del usuario.

    Reproduce la MISMA lógica que ``apps.accounts.serializers.get_user_role``:

    1. Si el usuario pertenece a un Group cuyo nombre coincide con un rol
       válido (recorriendo el orden de ``VALID_ROLES``), devuelve ese rol.
    2. Si el usuario es ``is_staff`` (sin group explícito), devuelve
       ``admin`` (compatibilidad con usuarios staff creados sin group).
    3. En caso contrario, devuelve ``DEFAULT_ROLE`` (``subscriber``).

    El rol legado ``"user"`` no es un valor posible de retorno de esta
    función (nunca se devuelve ``"user"``); si un rol de entrada fuera
    ``"user"``, ``normalize_role`` lo mapea a ``subscriber``.
    """
    if user is None or not getattr(user, "is_authenticated", True):
        return DEFAULT_ROLE

    # 1. Groups con nombre de rol válido (mismo orden que get_user_role).
    user_groups = set(user.groups.values_list("name", flat=True))
    for role in VALID_ROLES:
        if role in user_groups:
            return role

    # 2. Compatibilidad: staff sin group -> admin.
    if getattr(user, "is_staff", False):
        return "admin"

    # 3. Fallback.
    return DEFAULT_ROLE


def user_has_permission(user, permission):
    """Indica si el usuario efectivamente posee el permiso atómico indicado.

    - Devuelve ``False`` para usuarios anónimos / no autenticados.
    - Devuelve ``False`` para permisos desconocidos (no en ``PERMISSIONS``).
    - El resto se decide a partir de ``ROLE_PERMISSIONS[get_effective_role(user)]``.
    """
    if not permission or permission not in PERMISSIONS:
        return False
    if not getattr(user, "is_authenticated", False):
        return False
    role = get_effective_role(user)
    return permission in ROLE_PERMISSIONS.get(role, set())
