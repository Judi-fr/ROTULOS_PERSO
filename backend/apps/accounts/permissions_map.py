"""Mapa de permisos basado en roles.

Fuente única de verdad del rol efectivo y de los permisos atómicos por rol.
``get_effective_role`` y ``user_has_permission`` (o su envoltorio DRF,
``role_permissions.HasRolePermission``) son el mecanismo de permisos que
efectivamente usan hoy los endpoints: ``viewsets.UserAdminViewSet``,
``profile_views.ProfileView``, ``support_views.SupportMessageView`` y
``apps.orders`` (direcciones/pedidos) llaman a este módulo por acción, así
que sí gobierna el comportamiento real de la API.

Detalles de la auditoría (ver Parte A) que justifican esta implementación:

- El modelo ``User`` del proyecto **no posee un campo ``role``**. El rol se
  determina exclusivamente por **Django Groups** (nombrados como el rol) y
  el fallback ``is_staff``.
- Roles válidos del sistema: admin y subscriber (en pantalla "Comerciante",
  el dueño de un comercio). Desde 2026-10-21 no hay más "designer" ni
  "operator": con los comercios con equipo, lo que hace cada empleado lo
  deciden los perfiles del comercio, y esos dos roles quedaron repitiendo lo
  mismo. Sus usuarios pasaron a subscriber (migración 0030). Lo que no es
  comerciante ni admin completo es un rol PERSONALIZADO: personal de la
  plataforma con una parte del admin.
- ``DEFAULT_ROLE`` (fallback) = subscriber.
- El rol legado ``"user"`` se interpreta como ``subscriber``, y también los
  viejos ``"designer"``/``"operator"`` (por si queda un Group con ese nombre).
"""

from django.contrib.auth import get_user_model

try:
    from .models import GroupRolePermission, RolePermission
except Exception:  # pragma: no cover - import temprano durante migraciones
    GroupRolePermission = None
    RolePermission = None

User = get_user_model()

# Roles válidos del sistema (coinciden con ROLE_CHOICES del backend).
VALID_ROLES = ("admin", "subscriber")
DEFAULT_ROLE = "subscriber"

# Alias legacy que deben mapearse a un rol válido.
# "user" era el rol genérico antiguo y ahora equivale a subscriber.
LEGACY_ROLE_ALIASES = {
    "user": DEFAULT_ROLE,
    "administrador": "admin",
    "administradores": "admin",
    "administrator": "admin",
    "comerciante": DEFAULT_ROLE,
    "comerciantes": DEFAULT_ROLE,
    # Roles que existieron hasta 2026-10-21 (migración 0030).
    "designer": DEFAULT_ROLE,
    "diseñador": DEFAULT_ROLE,
    "diseñadores": DEFAULT_ROLE,
    "disenador": DEFAULT_ROLE,
    "disenadores": DEFAULT_ROLE,
    "operator": DEFAULT_ROLE,
    "operador": DEFAULT_ROLE,
    "operadores": DEFAULT_ROLE,
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
        # Carga operativa de pedidos (admin + operator, ver
        # 0020_seed_order_ingestion_permissions): alta manual a nombre
        # propio, importación CSV/Excel y plantillas de mapeo guardadas.
        "orders.create_manual",
        "orders.import",
        "orders.import_mappings",
        # Contacto/soporte desde el dashboard
        "support.create",
        # Rótulos propios (self-service)
        "labels.view",
        "labels.create",
        "labels.edit",
        "labels.delete",
        "labels.render",
        "labels.batch",
        "labels.template_create",
        # Plantillas por elementos / catálogo de variables y archivos fuente
        # (funcionalidades integradas desde backend_echu).
        "plantillas.view",
        "plantillas.edit",
        "variables.view",
        "documents.upload",
        "processing.import",
        # Documentos generados (hoy: lotes de rótulos) propios
        "documents.view",
        "documents.delete",
        # Auditoría, pedidos de todos los usuarios, bandeja de soporte,
        # rótulos de todos los usuarios / gestión de plantillas y
        # documentos de todos los usuarios (panel del admin): exclusivamente
        # admin, ver 0014_seed_admin_only_permissions, 0015_seed_label_permissions
        # y 0017_seed_document_permissions.
        "audit.view",
        "orders.view_all",
        "support.view_all",
        "support.manage",
        "labels.view_all",
        "labels.manage_templates",
        # Catálogos globales (transportistas manuales, tamaños de rótulo).
        "catalogs.manage",
        # Estado del sistema y alertas de disponibilidad (apps.monitoring).
        "monitoring.view",
        "variables.manage",
        "documents.view_all",
        # Dar de alta un pedido a nombre de OTRO usuario (0020): exclusivo
        # admin, ni siquiera operator lo tiene (ver ManualOrderCreateView).
        "orders.create_for_others",
        # ABM de credenciales/webhooks de integraciones (0021): exclusivo
        # admin, ver apps.integrations.
        "integrations.manage",
        # Comercios con equipo (apps/accounts/teams.py): "crear pedidos"
        # habilitaba a la vez tiendas, transportistas, tarifas y despacho, y
        # el dueño necesita poder darlos por separado (migración 0028).
        "orders.dispatch",
        "orders.export",
        "stores.manage",
        "carriers.manage",
        "printers.manage",
        "account.manage",
        "team.manage",
        # Partidos de otros más gruesos (migración 0029, 2026-10-21): cargar
        # seguimientos y la planilla de retiro (antes "despachar"), editar y
        # borrar plantillas (antes "crear plantillas"), borrar usuarios para
        # siempre (antes "desactivar"), exportar usuarios (antes "ver"),
        # exportar la auditoría (antes "verla") y la pantalla Reportes (antes
        # un permiso distinto por bloque).
        "orders.tracking_import",
        "orders.manifest",
        "labels.template_edit",
        "labels.template_delete",
        "users.delete",
        "users.export",
        "audit.export",
        "reports.view",
        # Estadísticas del comercio, un permiso por bloque (migración 0037).
        "stats.dispatch",
        "stats.carriers",
        "stats.buyers",
    }
)

# Permisos de self-service del comerciante (hasta 2026-10-21 eran también los
# de designer y operator, que ya no existen).
_SELF_SERVICE_PERMISSIONS = {
    "users.me.view",
    "users.me.edit",
    "users.me.change_password",
    "addresses.manage",
    "orders.view",
    "orders.create",
    "orders.cancel",
    "support.create",
    "labels.view",
    "labels.create",
    "labels.edit",
    "labels.delete",
    "labels.render",
    "labels.batch",
    "labels.template_create",
    "plantillas.view",
    "plantillas.edit",
    "variables.view",
    "documents.upload",
    "processing.import",
    "documents.view",
    "documents.delete",
}

# Lo que se partió de "orders.create" / "labels.batch" / "users.me.edit"
# (migración 0028): quien tenía aquello tiene esto.
_SELF_SERVICE_PERMISSIONS |= {
    "orders.dispatch",
    "orders.export",
    "stores.manage",
    "carriers.manage",
    "printers.manage",
    "account.manage",
    "team.manage",
}

# Lo que se partió de "orders.dispatch" / "labels.template_create" (migración
# 0029): quien tenía aquello tiene esto.
_SELF_SERVICE_PERMISSIONS |= {
    "orders.tracking_import",
    "orders.manifest",
    "labels.template_edit",
    "labels.template_delete",
}

# Estadísticas del comercio (migración 0037): quien ve pedidos las tiene; el
# dueño decide en los perfiles qué empleado ve cada bloque.
_SELF_SERVICE_PERMISSIONS |= {
    "stats.dispatch",
    "stats.carriers",
    "stats.buyers",
}

# Más la carga OPERATIVA de pedidos (alta manual, importación CSV/Excel,
# plantillas de mapeo) — ver 0020_seed_order_ingestion_permissions; el nombre
# viene de cuando era solo del rol "operator". "orders.create_for_others"
# queda afuera: eso es exclusivo admin.
_OPERATOR_PERMISSIONS = _SELF_SERVICE_PERMISSIONS | {
    "orders.create_manual",
    "orders.import",
    "orders.import_mappings",
}

# Mapeo rol -> conjunto de permisos.
# Es solo el fallback de antes de que exista GroupRolePermission:
#   - admin: acceso completo (el CRUD admin está protegido por IsAdminUser).
#   - subscriber (comerciante): self-service + carga operativa de pedidos.
# Un rol personalizado no tiene fallback: sus permisos son los de la tabla.
ROLE_PERMISSIONS = {
    "admin": set(PERMISSIONS),
    # El dueño de un comercio tiene todo lo del comercio (diseño de equipos),
    # incluido cargar a mano e importar pedidos.
    "subscriber": set(_OPERATOR_PERMISSIONS),
}

# Permisos del TRABAJO DEL COMERCIO → el acceso del equipo que además exige
# (apps/accounts/teams.py). Los que no están acá (perfil propio, soporte, todo
# lo de admin) dependen solo del rol.
TEAM_SCOPED_PERMISSIONS = {
    "orders.view": "orders.view",
    "addresses.manage": "orders.view",
    "documents.view": "orders.view",
    "labels.batch": "labels.print",
    "labels.render": "labels.print",
    # Borrar lotes es su propio acceso desde 2026-10-21: imprimir sin poder
    # borrar lo generado.
    "documents.delete": "documents.delete",
    "orders.dispatch": "orders.dispatch",
    "orders.tracking_import": "orders.dispatch",
    "orders.manifest": "orders.dispatch",
    "orders.cancel": "orders.cancel",
    "orders.create": "orders.import",
    "orders.create_manual": "orders.import",
    "orders.import": "orders.import",
    "orders.import_mappings": "orders.import",
    "labels.view": "templates",
    "labels.create": "templates",
    "labels.edit": "templates",
    "labels.delete": "templates",
    "labels.template_create": "templates",
    "labels.template_edit": "templates",
    "labels.template_delete": "templates",
    "plantillas.view": "templates",
    "plantillas.edit": "templates",
    "variables.view": "templates",
    "documents.upload": "templates",
    "processing.import": "templates",
    "stores.manage": "stores",
    "carriers.manage": "carriers",
    "printers.manage": "printers",
    "account.manage": "account",
    "team.manage": "team",
    "orders.export": "orders.export",
    "stats.dispatch": "stats.dispatch",
    "stats.carriers": "stats.carriers",
    "stats.buyers": "stats.buyers",
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
    # El rol es el techo; dentro del comercio, además, decide el equipo.
    if not _role_has_permission(user, permission):
        return False
    access = TEAM_SCOPED_PERMISSIONS.get(permission)
    if access is None:
        return True
    from .teams import can_access, membership

    # El admin no necesita comercio: sin uno conserva todo lo de su rol (lo
    # que lee o crea datos igual responde ``no_team``). En un comercio, como
    # cualquiera, vale lo que tiene ahí.
    if get_effective_role(user) == "admin" and membership(user) is None:
        return True
    return can_access(user, access)


def _role_has_permission(user, permission):
    """Lo que el rol global permite (el techo que fija el admin en roles.html).

    El admin tiene TODO, siempre, sin mirar la tabla (2026-10-21, pedido del
    usuario: "el admin es el leviatán"): su rol no se puede editar
    (``RolePermissionsView``) y un permiso nuevo lo tiene sin que una
    migración se lo asigne. Así no hay forma de dejar la plataforma sin
    alguien que la administre."""
    if get_effective_role(user) == "admin":
        return True
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
