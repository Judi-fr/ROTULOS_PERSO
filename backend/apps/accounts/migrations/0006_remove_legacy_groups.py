"""Elimina los Groups legacy y reasigna sus usuarios al Group canónico.

Los Groups ``administradores``, ``operadores`` y ``diseniadores`` fueron
sembrados por la migración 0001_seed_roles y quedaron obsoletos cuando se
introdujeron los roles canónicos (admin, designer, operator, subscriber).

Esta migración:

1. Reasigna los usuarios de cada Group legacy al Group canónico equivalente:
   - administradores -> admin
   - operadores      -> operator
   - diseniadores    -> designer
2. Elimina los Groups legacy de la base.

Los nombres legacy NO deben volver a crearse: la capa de roles
(``role_permission_views.PROTECTED_ROLE_NAMES``) ya los bloquea.
"""

from django.db import migrations

# Group legacy -> Group canónico equivalente.
LEGACY_TO_CANONICAL = {
    "administradores": "admin",
    "operadores": "operator",
    "diseñadores": "designer",
}

LEGACY_NAMES = tuple(LEGACY_TO_CANONICAL.keys())


def remove_legacy_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    for legacy_name, canonical_name in LEGACY_TO_CANONICAL.items():
        legacy = Group.objects.filter(name=legacy_name).first()
        if legacy is None:
            continue

        canonical, _ = Group.objects.get_or_create(name=canonical_name)

        # Reasignar usuarios del Group legacy al canónico.
        user_ids = legacy.user_set.values_list("id", flat=True)
        for user in User.objects.filter(id__in=user_ids):
            user.groups.add(canonical)

        # Eliminar el Group legacy (los usuarios NO se borran).
        legacy.delete()


def restore_legacy_groups(apps, schema_editor):
    """Reversión: recrea los Groups legacy (sin reasignar usuarios)."""
    Group = apps.get_model("auth", "Group")
    for name in LEGACY_NAMES:
        Group.objects.get_or_create(name=name)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_seed_role_permissions"),
    ]

    operations = [
        migrations.RunPython(remove_legacy_groups, restore_legacy_groups),
    ]