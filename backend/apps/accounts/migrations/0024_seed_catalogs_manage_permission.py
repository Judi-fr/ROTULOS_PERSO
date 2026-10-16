"""Siembra 'catalogs.manage' (admin-only): administrar los catálogos globales
de transportistas manuales y tamaños de rótulo (T#354-356). Idempotente,
mismo patrón que las siembras anteriores."""

from django.db import migrations

PERMISSION = {
    "key": "catalogs.manage",
    "name": "Administrar catálogos (transportistas manuales y tamaños de rótulo)",
    "category": "catalogs",
}


def seed(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    RolePermission = apps.get_model("accounts", "RolePermission")
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    admin, _ = Group.objects.get_or_create(name="admin")
    permission, _ = RolePermission.objects.get_or_create(
        key=PERMISSION["key"], defaults={"name": PERMISSION["name"], "category": PERMISSION["category"]}
    )
    GroupRolePermission.objects.get_or_create(group=admin, permission=permission)


def unseed(apps, schema_editor):
    apps.get_model("accounts", "GroupRolePermission").objects.filter(permission__key=PERMISSION["key"]).delete()
    apps.get_model("accounts", "RolePermission").objects.filter(key=PERMISSION["key"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0023_account_settings"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
