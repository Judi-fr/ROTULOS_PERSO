"""Siembra el permiso 'users.unlock' (desbloqueo manual de cuentas) y lo
asigna solo al rol admin — mismo criterio que 'users.reactivate' /
'users.deactivate' en 0005_seed_role_permissions.py.

Idempotente: mismo patrón que 0007_seed_order_permissions.py.
"""

from django.db import migrations

PERMISSION = {"key": "users.unlock", "name": "Desbloquear usuarios", "category": "users"}


def seed_unlock_permission(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    RolePermission = apps.get_model("accounts", "RolePermission")
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")

    admin_group, _ = Group.objects.get_or_create(name="admin")
    permission, _ = RolePermission.objects.get_or_create(
        key=PERMISSION["key"],
        defaults={"name": PERMISSION["name"], "category": PERMISSION["category"]},
    )
    GroupRolePermission.objects.get_or_create(group=admin_group, permission=permission)


def unseed_unlock_permission(apps, schema_editor):
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    GroupRolePermission.objects.filter(permission__key=PERMISSION["key"]).delete()
    RolePermission.objects.filter(key=PERMISSION["key"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0008_loginlockout"),
    ]

    operations = [
        migrations.RunPython(seed_unlock_permission, unseed_unlock_permission),
    ]
