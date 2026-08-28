"""Siembra los permisos de direcciones/pedidos propios y los asigna a los
cuatro roles existentes (self-service, igual que 'users.me.*').

Idempotente: mismo patrón que 0005_seed_role_permissions.py.
"""

from django.db import migrations

ROLE_NAMES = ("admin", "designer", "operator", "subscriber")

NEW_PERMISSIONS = [
    {"key": "addresses.manage", "name": "Gestionar direcciones propias", "category": "orders"},
    {"key": "orders.view", "name": "Ver pedidos propios", "category": "orders"},
    {"key": "orders.create", "name": "Crear pedidos propios", "category": "orders"},
    {"key": "orders.cancel", "name": "Cancelar pedidos propios", "category": "orders"},
]


def seed_order_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    RolePermission = apps.get_model("accounts", "RolePermission")
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")

    groups = {}
    for name in ROLE_NAMES:
        group, _ = Group.objects.get_or_create(name=name)
        groups[name] = group

    permissions = {}
    for perm in NEW_PERMISSIONS:
        obj, _ = RolePermission.objects.get_or_create(
            key=perm["key"],
            defaults={"name": perm["name"], "category": perm["category"]},
        )
        permissions[perm["key"]] = obj

    # Los cuatro roles reciben los cuatro permisos nuevos (self-service).
    for role in ROLE_NAMES:
        group = groups[role]
        for perm in permissions.values():
            GroupRolePermission.objects.get_or_create(group=group, permission=perm)


def unseed_order_permissions(apps, schema_editor):
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    keys = [p["key"] for p in NEW_PERMISSIONS]
    GroupRolePermission.objects.filter(permission__key__in=keys).delete()
    RolePermission.objects.filter(key__in=keys).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_remove_legacy_groups"),
    ]

    operations = [
        migrations.RunPython(seed_order_permissions, unseed_order_permissions),
    ]
