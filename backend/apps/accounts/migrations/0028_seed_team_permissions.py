"""Permisos de los comercios con equipo (ver apps/accounts/teams.py).

"orders.create" habilitaba a la vez tiendas, transportistas, tarifas y
despacho; se parte en permisos por área para que el dueño los pueda dar por
separado. Quien tenía el permiso viejo recibe los nuevos, así nadie pierde
nada. Además "subscriber" (el comerciante) recibe cargar a mano e importar
pedidos: el diseño dice que el dueño tiene todo lo de su comercio.
"""

from django.db import migrations

NEW_PERMISSIONS = {
    "orders.dispatch": ("Despachar pedidos y crear envíos", "orders"),
    "orders.export": ("Exportar pedidos", "orders"),
    "stores.manage": ("Conectar y configurar tiendas", "integrations"),
    "carriers.manage": ("Cuentas de transportistas y tarifas", "integrations"),
    "printers.manage": ("Impresoras y agentes de impresión", "labels"),
    "account.manage": ("Editar los datos del comercio", "users"),
    "team.manage": ("Administrar el equipo del comercio", "users"),
}
# permiso que ya tenía el grupo → permisos nuevos que recibe
SPLIT_FROM = {
    "orders.create": ["orders.dispatch", "orders.export", "stores.manage", "carriers.manage", "team.manage"],
    "labels.batch": ["printers.manage"],
    "users.me.edit": ["account.manage"],
}
SUBSCRIBER_EXTRA = ["orders.create_manual", "orders.import", "orders.import_mappings"]


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

    subscriber, _ = Group.objects.get_or_create(name="subscriber")
    for key in SUBSCRIBER_EXTRA:
        permission = RolePermission.objects.filter(key=key).first()
        if permission:
            GroupRolePermission.objects.get_or_create(group=subscriber, permission=permission)


def unseed(apps, schema_editor):
    GroupRolePermission = apps.get_model("accounts", "GroupRolePermission")
    GroupRolePermission.objects.filter(permission__key__in=list(NEW_PERMISSIONS)).delete()
    GroupRolePermission.objects.filter(group__name="subscriber", permission__key__in=SUBSCRIBER_EXTRA).delete()
    apps.get_model("accounts", "RolePermission").objects.filter(key__in=list(NEW_PERMISSIONS)).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0027_create_teams_for_users"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
