"""Quita los permisos de los diseños por elementos: no quedó nada que controlen, y en
la pantalla de Roles parecían servir para algo. Lo que ya estaba asignado se borra
con ellos (GroupRolePermission cuelga en cascada)."""
from django.db import migrations

REMOVED = ("plantillas.view", "plantillas.edit", "variables.view", "variables.manage")


def remove_permissions(apps, schema_editor):
    apps.get_model("accounts", "GroupRolePermission").objects.filter(permission__key__in=REMOVED).delete()
    apps.get_model("accounts", "RolePermission").objects.filter(key__in=REMOVED).delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0037_seed_stats_permissions")]

    operations = [migrations.RunPython(remove_permissions, migrations.RunPython.noop)]
