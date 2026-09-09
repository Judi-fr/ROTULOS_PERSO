"""Siembra los permisos de rótulos (apps.labels).

'labels.view'/'create'/'edit'/'delete' van a los cuatro roles canónicos
(self-service, igual que 'orders.*'/'support.create'). 'labels.view_all' y
'labels.manage_templates' van SOLO al rol admin (igual que
0014_seed_admin_only_permissions).

Idempotente: mismo patrón que 0007_seed_order_permissions.py /
0012_seed_support_permission.py / 0014_seed_admin_only_permissions.py. No
toca roles personalizados (p. ej. "productor", "creador"): esos permisos se
asignan a mano desde la pantalla de Roles si corresponde.
"""

from django.db import migrations

ROLE_NAMES = ("admin", "designer", "operator", "subscriber")

SELF_SERVICE_PERMISSIONS = [
    {"key": "labels.view", "name": "Ver rótulos propios", "category": "labels"},
    {"key": "labels.create", "name": "Crear rótulos", "category": "labels"},
    {"key": "labels.edit", "name": "Editar rótulos propios", "category": "labels"},
    {"key": "labels.delete", "name": "Eliminar rótulos propios", "category": "labels"},
]

ADMIN_ONLY_PERMISSIONS = [
    {"key": "labels.view_all", "name": "Ver rótulos de todos los usuarios", "category": "labels"},
    {"key": "labels.manage_templates", "name": "Gestionar plantillas de rótulo", "category": "labels"},
]


def seed_label_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    RolePermission = apps.get_model("accounts", "RolePermission")
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")

    groups = {}
    for name in ROLE_NAMES:
        group, _ = Group.objects.get_or_create(name=name)
        groups[name] = group

    for perm in SELF_SERVICE_PERMISSIONS:
        obj, _ = RolePermission.objects.get_or_create(
            key=perm["key"], defaults={"name": perm["name"], "category": perm["category"]}
        )
        for role in ROLE_NAMES:
            GroupRolePermission.objects.get_or_create(group=groups[role], permission=obj)

    for perm in ADMIN_ONLY_PERMISSIONS:
        obj, _ = RolePermission.objects.get_or_create(
            key=perm["key"], defaults={"name": perm["name"], "category": perm["category"]}
        )
        GroupRolePermission.objects.get_or_create(group=groups["admin"], permission=obj)


def unseed_label_permissions(apps, schema_editor):
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    keys = [p["key"] for p in SELF_SERVICE_PERMISSIONS + ADMIN_ONLY_PERMISSIONS]
    GroupRolePermission.objects.filter(permission__key__in=keys).delete()
    RolePermission.objects.filter(key__in=keys).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_seed_admin_only_permissions"),
    ]

    operations = [
        migrations.RunPython(seed_label_permissions, unseed_label_permissions),
    ]
