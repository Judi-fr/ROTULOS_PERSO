"""Siembra los permisos de plantillas por elementos, variables, documentos
fuente e importación (funcionalidades integradas desde backend_echu).

Self-service (los cuatro roles canónicos):
  - plantillas.view       ver/renderizar plantillas propias
  - plantillas.edit       crear/editar/borrar plantillas propias
  - variables.view        leer el catálogo de variables
  - documents.upload      subir/ver/borrar archivos fuente (Documento)
  - processing.import     lanzar importaciones (Claude lee el rótulo)

Admin-only:
  - variables.manage      crear/editar/borrar variables del catálogo

Idempotente: mismo patrón que las migraciones de siembra anteriores. No toca
roles personalizados: esos permisos se asignan a mano desde la pantalla de
Roles si corresponde.
"""

from django.db import migrations

ROLE_NAMES = ("admin", "designer", "operator", "subscriber")

SELF_SERVICE_PERMISSIONS = [
    {"key": "plantillas.view", "name": "Ver plantillas de rótulo", "category": "labels"},
    {"key": "plantillas.edit", "name": "Crear y editar plantillas de rótulo", "category": "labels"},
    {"key": "variables.view", "name": "Ver catálogo de variables de rótulo", "category": "labels"},
    {"key": "documents.upload", "name": "Subir archivos fuente para importar", "category": "documents"},
    {"key": "processing.import", "name": "Importar rótulos desde una foto", "category": "processing"},
]

ADMIN_ONLY_PERMISSIONS = [
    {"key": "variables.manage", "name": "Administrar catálogo de variables de rótulo", "category": "labels"},
]


def seed_permissions(apps, schema_editor):
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


def unseed_permissions(apps, schema_editor):
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    RolePermission = apps.get_model("accounts", "RolePermission")

    keys = [p["key"] for p in SELF_SERVICE_PERMISSIONS + ADMIN_ONLY_PERMISSIONS]
    GroupRolePermission.objects.filter(permission__key__in=keys).delete()
    RolePermission.objects.filter(key__in=keys).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0021_seed_integrations_manage_permission"),
    ]

    operations = [
        migrations.RunPython(seed_permissions, unseed_permissions),
    ]