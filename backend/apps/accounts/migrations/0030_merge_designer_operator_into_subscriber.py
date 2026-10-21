"""Quedan dos roles fijos: admin y subscriber ("Comerciante") (2026-10-21).

Con los comercios con equipo, lo que hace cada empleado lo deciden los
perfiles del comercio, y "designer"/"operator" repetían al comerciante con
pequeñas diferencias. Sus usuarios pasan a subscriber, con todo lo de su
comercio; los permisos de administración que se les hubieran agregado a mano
se pierden con el rol (decidido con el usuario). Los Groups se borran, y con
ellos sus GroupRolePermission. Si alguna vez vuelve a aparecer un Group con
ese nombre, ``permissions_map.normalize_role`` lo trata como subscriber.

No tiene vuelta atrás exacta: al revertir se recrean los dos Groups vacíos,
sin sus usuarios.
"""

from django.db import migrations

OLD_ROLES = ("designer", "operator")


def merge(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    subscriber, _ = Group.objects.get_or_create(name="subscriber")
    for group in Group.objects.filter(name__in=OLD_ROLES):
        for user in group.user_set.all():
            user.groups.add(subscriber)
        group.delete()


def recreate(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name in OLD_ROLES:
        Group.objects.get_or_create(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0029_seed_split_permissions"),
    ]

    operations = [
        migrations.RunPython(merge, recreate),
    ]
