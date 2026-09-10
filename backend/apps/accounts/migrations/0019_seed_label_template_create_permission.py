"""Siembra el permiso 'labels.template_create' (crear/editar plantillas
PROPIAS de rótulo).

Antes, crear cualquier plantilla exigía 'labels.manage_templates' (solo
admin) sin ninguna pantalla detrás, así que nadie podía crear plantillas.
Se parte en dos: este permiso, self-service (cuatro roles canónicos), cubre
plantillas propias/privadas; 'labels.manage_templates' (0015) queda
exclusivamente para tocar plantillas PÚBLICAS o de otro usuario, o marcar
una como pública (ver apps.labels.views.LabelTemplateViewSet).

Idempotente: mismo patrón que 0018_seed_labels_batch_permission.py.
"""

from django.db import migrations

ROLE_NAMES = ("admin", "designer", "operator", "subscriber")

PERMISSION = {
    "key": "labels.template_create",
    "name": "Crear y editar plantillas propias",
    "category": "labels",
}


def seed_permission(apps, schema_editor):
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


def unseed_permission(apps, schema_editor):
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    GroupRolePermission.objects.filter(permission__key=PERMISSION["key"]).delete()
    RolePermission.objects.filter(key=PERMISSION["key"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0018_seed_labels_batch_permission"),
    ]

    operations = [
        migrations.RunPython(seed_permission, unseed_permission),
    ]
