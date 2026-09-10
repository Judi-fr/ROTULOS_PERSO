"""Siembra el permiso 'integrations.manage' (ABM de claves de API y
webhooks, ver apps.integrations): exclusivamente admin.

Idempotente: mismo patrón que las migraciones de siembra anteriores.
"""

from django.db import migrations

PERMISSION = {
    "key": "integrations.manage",
    "name": "Gestionar claves de API y webhooks",
    "category": "integrations",
}


def seed_permission(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    RolePermission = apps.get_model("accounts", "RolePermission")
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")

    admin_group, _ = Group.objects.get_or_create(name="admin")
    obj, _ = RolePermission.objects.get_or_create(
        key=PERMISSION["key"],
        defaults={"name": PERMISSION["name"], "category": PERMISSION["category"]},
    )
    GroupRolePermission.objects.get_or_create(group=admin_group, permission=obj)


def unseed_permission(apps, schema_editor):
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    GroupRolePermission.objects.filter(permission__key=PERMISSION["key"]).delete()
    RolePermission.objects.filter(key=PERMISSION["key"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0020_seed_order_ingestion_permissions"),
    ]

    operations = [
        migrations.RunPython(seed_permission, unseed_permission),
    ]
