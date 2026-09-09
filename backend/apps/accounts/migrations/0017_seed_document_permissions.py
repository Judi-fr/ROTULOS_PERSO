"""Siembra los permisos de documentos (apps.documents).

'documents.view'/'delete' van a los cuatro roles canónicos (self-service,
igual que 'labels.*'). 'documents.view_all' va SOLO al rol admin (igual
que 0014_seed_admin_only_permissions/0015_seed_label_permissions).

Idempotente: mismo patrón que las migraciones de siembra anteriores.
"""

from django.db import migrations

ROLE_NAMES = ("admin", "designer", "operator", "subscriber")

SELF_SERVICE_PERMISSIONS = [
    {"key": "documents.view", "name": "Ver documentos propios", "category": "documents"},
    {"key": "documents.delete", "name": "Eliminar documentos propios", "category": "documents"},
]

ADMIN_ONLY_PERMISSIONS = [
    {
        "key": "documents.view_all",
        "name": "Ver documentos de todos los usuarios",
        "category": "documents",
    },
]


def seed_document_permissions(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    RolePermission = apps.get_model("accounts", "RolePermission")
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")

    groups = {}
    for name in ROLE_NAMES:
        group, _ = Group.objects.get_or_create(name=name)
        groups[name] = group

    for perm in SELF_SERVICE_PERMISSIONS:
        obj, _ = RolePermission.objects.get_or_create(
            key=perm["key"], defaults={"name": perm["name"], "category": perm["category"]}
        )
        for role in ROLE_NAMES:
            GroupRolePermission.objects.get_or_create(group=groups[role], permission=obj)

    for perm in ADMIN_ONLY_PERMISSIONS:
        obj, _ = RolePermission.objects.get_or_create(
            key=perm["key"], defaults={"name": perm["name"], "category": perm["category"]}
        )
        GroupRolePermission.objects.get_or_create(group=groups["admin"], permission=obj)


def unseed_document_permissions(apps, schema_editor):
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    keys = [p["key"] for p in SELF_SERVICE_PERMISSIONS + ADMIN_ONLY_PERMISSIONS]
    GroupRolePermission.objects.filter(permission__key__in=keys).delete()
    RolePermission.objects.filter(key__in=keys).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0016_seed_label_render_permission"),
    ]

    operations = [
        migrations.RunPython(seed_document_permissions, unseed_document_permissions),
    ]
