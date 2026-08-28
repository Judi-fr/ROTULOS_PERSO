"""Siembra el permiso 'support.create' (form de contacto del dashboard) y lo
asigna a los cuatro roles existentes (self-service, igual que 'orders.*').

Idempotente: mismo patrón que 0007_seed_order_permissions.py.
"""

from django.db import migrations

ROLE_NAMES = ("admin", "designer", "operator", "subscriber")

NEW_PERMISSIONS = [
    {"key": "support.create", "name": "Enviar mensaje de soporte", "category": "support"},
]


def seed_support_permission(apps, schema_editor):
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

    for role in ROLE_NAMES:
        group = groups[role]
        for perm in permissions.values():
            GroupRolePermission.objects.get_or_create(group=group, permission=perm)


def unseed_support_permission(apps, schema_editor):
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    keys = [p["key"] for p in NEW_PERMISSIONS]
    GroupRolePermission.objects.filter(permission__key__in=keys).delete()
    RolePermission.objects.filter(key__in=keys).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_supportmessage"),
    ]

    operations = [
        migrations.RunPython(seed_support_permission, unseed_support_permission),
    ]
