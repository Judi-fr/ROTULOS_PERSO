"""Siembra 'monitoring.view' (admin-only): ver el estado del sistema y mandar
el mail de prueba de las alertas (T#193). Mismo patrón que 0024."""

from django.db import migrations

PERMISSION = {
    "key": "monitoring.view",
    "name": "Ver el estado del sistema y las alertas",
    "category": "monitoring",
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
        ("accounts", "0024_seed_catalogs_manage_permission"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
