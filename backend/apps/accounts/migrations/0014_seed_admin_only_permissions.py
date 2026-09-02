"""Siembra los permisos exclusivos del admin (auditoría, bandeja de soporte
y pedidos de todos los usuarios) y los asigna SOLO al rol admin.

Idempotente: mismo patrón que 0007_seed_order_permissions.py /
0012_seed_support_permission.py.
"""

from django.db import migrations

NEW_PERMISSIONS = [
    {"key": "audit.view", "name": "Ver registros de auditoría", "category": "audit"},
    {"key": "orders.view_all", "name": "Ver pedidos de todos los usuarios", "category": "orders"},
    {"key": "support.view_all", "name": "Ver todos los mensajes de soporte", "category": "support"},
    {"key": "support.manage", "name": "Gestionar mensajes de soporte", "category": "support"},
]


def seed_admin_only_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    RolePermission = apps.get_model("accounts", "RolePermission")
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")

    admin_group, _ = Group.objects.get_or_create(name="admin")

    for perm in NEW_PERMISSIONS:
        obj, _ = RolePermission.objects.get_or_create(
            key=perm["key"],
            defaults={"name": perm["name"], "category": perm["category"]},
        )
        GroupRolePermission.objects.get_or_create(group=admin_group, permission=obj)


def unseed_admin_only_permissions(apps, schema_editor):
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    keys = [p["key"] for p in NEW_PERMISSIONS]
    GroupRolePermission.objects.filter(permission__key__in=keys).delete()
    RolePermission.objects.filter(key__in=keys).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0013_support_message_admin_fields"),
    ]

    operations = [
        migrations.RunPython(seed_admin_only_permissions, unseed_admin_only_permissions),
    ]
