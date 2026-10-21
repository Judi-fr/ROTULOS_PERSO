"""Permisos partidos de otros más gruesos (2026-10-21, pedido del usuario:
"pedidos, rótulos y admin más finos").

Quien tenía el permiso viejo recibe los nuevos, así nadie pierde nada; el
admin después los saca rol por rol en roles.html. Además, "Borrar lotes" pasa
a ser un acceso de equipo propio (antes venía con "Imprimir rótulos"): los
perfiles, empleados e invitaciones pendientes que imprimían lo reciben.
"""

from django.db import migrations

NEW_PERMISSIONS = {
    "orders.tracking_import": ("Cargar seguimientos desde planilla", "orders"),
    "orders.manifest": ("Generar la planilla de retiro", "orders"),
    "labels.template_edit": ("Editar plantillas y restaurar versiones", "labels"),
    "labels.template_delete": ("Borrar plantillas", "labels"),
    "users.delete": ("Borrar usuarios para siempre", "users"),
    "users.export": ("Exportar usuarios", "users"),
    "audit.export": ("Exportar la auditoría", "audit"),
    "reports.view": ("Ver reportes", "reports"),
}
# permiso que ya tenía el grupo → permisos nuevos que recibe
SPLIT_FROM = {
    "orders.dispatch": ["orders.tracking_import", "orders.manifest"],
    "labels.template_create": ["labels.template_edit", "labels.template_delete"],
    "users.deactivate": ["users.delete"],
    "users.view": ["users.export", "reports.view"],
    "orders.view_all": ["reports.view"],
    "support.view_all": ["reports.view"],
    "audit.view": ["audit.export", "reports.view"],
}
OLD_ACCESS = "labels.print"
NEW_ACCESS = "documents.delete"


def seed(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    RolePermission = apps.get_model("accounts", "RolePermission")
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")

    permissions = {}
    for key, (name, category) in NEW_PERMISSIONS.items():
        permissions[key], _ = RolePermission.objects.get_or_create(
            key=key, defaults={"name": name, "category": category}
        )

    for old_key, new_keys in SPLIT_FROM.items():
        group_ids = GroupRolePermission.objects.filter(permission__key=old_key).values_list("group_id", flat=True)
        for group in Group.objects.filter(id__in=list(group_ids)):
            for key in new_keys:
                GroupRolePermission.objects.get_or_create(group=group, permission=permissions[key])

    admin, _ = Group.objects.get_or_create(name="admin")
    for permission in permissions.values():
        GroupRolePermission.objects.get_or_create(group=admin, permission=permission)

    for model in ("TeamProfile", "TeamMember", "TeamInvitation"):
        for row in apps.get_model("accounts", model).objects.all():
            if OLD_ACCESS in (row.accesses or []) and NEW_ACCESS not in row.accesses:
                row.accesses = [*row.accesses, NEW_ACCESS]
                row.save(update_fields=["accesses"])


def unseed(apps, schema_editor):
    for model in ("TeamProfile", "TeamMember", "TeamInvitation"):
        for row in apps.get_model("accounts", model).objects.all():
            if NEW_ACCESS in (row.accesses or []):
                row.accesses = [access for access in row.accesses if access != NEW_ACCESS]
                row.save(update_fields=["accesses"])
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    GroupRolePermission.objects.filter(permission__key__in=list(NEW_PERMISSIONS)).delete()
    apps.get_model("accounts", "RolePermission").objects.filter(key__in=list(NEW_PERMISSIONS)).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0028_seed_team_permissions"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
