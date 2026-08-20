"""Siembra los roles (Groups), los permisos y las asignaciones iniciales.

Idempotente: usar get_or_create / get_or_create en la tabla intermedia hace que
reaplicar esta migración no duplique Groups, permisos ni relaciones.
"""

from django.db import migrations

# Los cuatro roles del sistema (Django Groups). Son fijos: no se crean ni se
# eliminan por API.
ROLE_NAMES = ("admin", "designer", "operator", "subscriber")

ROLE_LABELS = {
    "admin": "Administrador",
    "designer": "Diseñador",
    "operator": "Operador",
    "subscriber": "Suscriptor",
}

PERMISSIONS = [
    # Usuarios
    {"key": "users.view", "name": "Ver usuarios", "category": "users"},
    {"key": "users.create", "name": "Crear usuarios", "category": "users"},
    {"key": "users.edit", "name": "Editar usuarios", "category": "users"},
    {"key": "users.deactivate", "name": "Desactivar usuarios", "category": "users"},
    {"key": "users.reactivate", "name": "Reactivar usuarios", "category": "users"},
    # Perfil propio
    {"key": "users.me.view", "name": "Ver perfil propio", "category": "users"},
    {"key": "users.me.edit", "name": "Editar perfil propio", "category": "users"},
    {"key": "users.me.change_password", "name": "Cambiar contraseña propia", "category": "users"},
]

# Asignación inicial rol -> permisos.
INITIAL_ASSIGNMENTS = {
    "admin": {p["key"] for p in PERMISSIONS},  # todos los permisos
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


def seed_role_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    RolePermission = apps.get_model("accounts", "RolePermission")
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")

    # 1. Groups: idempotente.
    groups = {}
    for name in ROLE_NAMES:
        group, _ = Group.objects.get_or_create(name=name)
        groups[name] = group

    # 2. Permisos: idempotente.
    permissions = {}
    for perm in PERMISSIONS:
        obj, _ = RolePermission.objects.get_or_create(
            key=perm["key"],
            defaults={"name": perm["name"], "category": perm["category"]},
        )
        permissions[perm["key"]] = obj

    # 3. Asignaciones: idempotente.
    for role, keys in INITIAL_ASSIGNMENTS.items():
        group = groups[role]
        for key in keys:
            GroupRolePermission.objects.get_or_create(
                group=group,
                permission=permissions[key],
            )


def unseed_role_permissions(apps, schema_editor):
    """Reversión: borra las asignaciones y los permisos sembrados (no los Groups)."""
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    GroupRolePermission.objects.all().delete()
    RolePermission.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_rolepermission_grouprolepermission"),
    ]

    operations = [
        migrations.RunPython(seed_role_permissions, unseed_role_permissions),
    ]