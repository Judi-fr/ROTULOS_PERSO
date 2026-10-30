"""Estadísticas del comercio (2026-10-30): un permiso y un acceso de equipo por
bloque (despacho, transportistas, compradores), para que el dueño decida en
los perfiles quién ve cada uno.

Los grupos que ven pedidos reciben los tres permisos (el techo del rol). En
los equipos solo los recibe lo que ya tenía TODOS los accesos (el perfil
"Todo" aunque lo hayan renombrado, y los empleados o invitaciones con todo):
a "Depósito", "Atención" o un personalizado se los da el dueño si quiere.
"""

from django.db import migrations

NEW_PERMISSIONS = {
    "stats.dispatch": "Ver las estadísticas de despacho",
    "stats.carriers": "Ver las estadísticas de transportistas",
    "stats.buyers": "Ver las estadísticas de compradores",
}
# Los 13 accesos que existían: quien los tenía todos tenía "Todo".
PREVIOUS_ACCESSES = {
    "orders.view", "labels.print", "documents.delete", "orders.dispatch", "orders.cancel", "orders.import",
    "templates", "stores", "carriers", "printers", "account", "team", "orders.export",
}


def seed(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    RolePermission = apps.get_model("accounts", "RolePermission")
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")

    permissions = [
        RolePermission.objects.get_or_create(key=key, defaults={"name": name, "category": "orders"})[0]
        for key, name in NEW_PERMISSIONS.items()
    ]
    group_ids = set(GroupRolePermission.objects.filter(permission__key="orders.view").values_list("group_id", flat=True))
    group_ids.add(Group.objects.get_or_create(name="admin")[0].id)
    for group in Group.objects.filter(id__in=group_ids):
        for permission in permissions:
            GroupRolePermission.objects.get_or_create(group=group, permission=permission)

    for model in ("TeamProfile", "TeamMember", "TeamInvitation"):
        for row in apps.get_model("accounts", model).objects.all():
            current = set(row.accesses or [])
            if PREVIOUS_ACCESSES <= current:
                row.accesses = [*row.accesses, *[key for key in NEW_PERMISSIONS if key not in current]]
                row.save(update_fields=["accesses"])


def unseed(apps, schema_editor):
    for model in ("TeamProfile", "TeamMember", "TeamInvitation"):
        for row in apps.get_model("accounts", model).objects.all():
            if set(NEW_PERMISSIONS) & set(row.accesses or []):
                row.accesses = [access for access in row.accesses if access not in NEW_PERMISSIONS]
                row.save(update_fields=["accesses"])
    apps.get_model("accounts", "GroupRolePermission").objects.filter(permission__key__in=list(NEW_PERMISSIONS)).delete()
    apps.get_model("accounts", "RolePermission").objects.filter(key__in=list(NEW_PERMISSIONS)).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0036_login_lockout_progressive"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
