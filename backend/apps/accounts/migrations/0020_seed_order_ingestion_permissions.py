"""Siembra los permisos de carga operativa de pedidos (apps.orders).

'orders.create_manual'/'orders.import'/'orders.import_mappings' van a
admin + operator (carga operativa: alta manual de un envío, importación
CSV/Excel y plantillas de mapeo propias) — NO a designer/subscriber.
'orders.create_for_others' (dar de alta un pedido a nombre de OTRO
usuario) va SOLO a admin, ni operator lo tiene.

Idempotente: mismo patrón que las migraciones de siembra anteriores.
"""

from django.db import migrations

OPERATOR_ROLE_NAMES = ("admin", "operator")

OPERATOR_PERMISSIONS = [
    {"key": "orders.create_manual", "name": "Alta manual de un envío", "category": "orders"},
    {"key": "orders.import", "name": "Importar pedidos desde archivo", "category": "orders"},
    {
        "key": "orders.import_mappings",
        "name": "Guardar plantillas de mapeo de importación",
        "category": "orders",
    },
]

ADMIN_ONLY_PERMISSIONS = [
    {
        "key": "orders.create_for_others",
        "name": "Dar de alta pedidos a nombre de otro usuario",
        "category": "orders",
    },
]


def seed_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    RolePermission = apps.get_model("accounts", "RolePermission")
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")

    groups = {}
    for name in OPERATOR_ROLE_NAMES:
        group, _ = Group.objects.get_or_create(name=name)
        groups[name] = group

    for perm in OPERATOR_PERMISSIONS:
        obj, _ = RolePermission.objects.get_or_create(
            key=perm["key"], defaults={"name": perm["name"], "category": perm["category"]}
        )
        for role in OPERATOR_ROLE_NAMES:
            GroupRolePermission.objects.get_or_create(group=groups[role], permission=obj)

    for perm in ADMIN_ONLY_PERMISSIONS:
        obj, _ = RolePermission.objects.get_or_create(
            key=perm["key"], defaults={"name": perm["name"], "category": perm["category"]}
        )
        GroupRolePermission.objects.get_or_create(group=groups["admin"], permission=obj)


def unseed_permissions(apps, schema_editor):
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    keys = [p["key"] for p in OPERATOR_PERMISSIONS + ADMIN_ONLY_PERMISSIONS]
    GroupRolePermission.objects.filter(permission__key__in=keys).delete()
    RolePermission.objects.filter(key__in=keys).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0019_seed_label_template_create_permission"),
    ]

    operations = [
        migrations.RunPython(seed_permissions, unseed_permissions),
    ]
