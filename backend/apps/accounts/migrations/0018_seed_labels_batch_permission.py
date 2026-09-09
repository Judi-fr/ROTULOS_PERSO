"""Siembra el permiso 'labels.batch' (generación de rótulos por lote).

Self-service, igual que 'labels.view'/'create'/'edit'/'delete'/'render':
va a los cuatro roles canónicos.

Idempotente: mismo patrón que las migraciones de siembra anteriores.
"""

from django.db import migrations

ROLE_NAMES = ("admin", "designer", "operator", "subscriber")

PERMISSION = {
    "key": "labels.batch",
    "name": "Generar rótulos por lote",
    "category": "labels",
}


def seed_labels_batch_permission(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    RolePermission = apps.get_model("accounts", "RolePermission")
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")

    obj, _ = RolePermission.objects.get_or_create(
        key=PERMISSION["key"],
        defaults={"name": PERMISSION["name"], "category": PERMISSION["category"]},
    )
    for name in ROLE_NAMES:
        group, _ = Group.objects.get_or_create(name=name)
        GroupRolePermission.objects.get_or_create(group=group, permission=obj)


def unseed_labels_batch_permission(apps, schema_editor):
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    GroupRolePermission.objects.filter(permission__key=PERMISSION["key"]).delete()
    RolePermission.objects.filter(key=PERMISSION["key"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0017_seed_document_permissions"),
    ]

    operations = [
        migrations.RunPython(seed_labels_batch_permission, unseed_labels_batch_permission),
    ]
