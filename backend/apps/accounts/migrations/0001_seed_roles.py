"""Siembra los tres roles del proyecto como Groups de Django.

Se modelan como Groups (y no como flags o un modelo propio) porque el proyecto
usa el User por defecto: los Groups son el mecanismo nativo de Django para
agrupar usuarios y, más adelante, colgarles permisos.

    - administradores: gestión total (equivale a los usuarios is_staff).
    - diseñadores:     crean y editan rótulos/plantillas.
    - operadores:      operan el flujo diario (carga y procesamiento).

Idempotente: usa get_or_create, así reaplicarla no duplica ni pisa nada.
"""

from django.db import migrations

ROLES = ["administradores", "diseñadores", "operadores"]


def crear_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for nombre in ROLES:
        Group.objects.get_or_create(name=nombre)


def borrar_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=ROLES).delete()


class Migration(migrations.Migration):

    # El modelo Group se crea en auth.0001_initial; con esa dependencia basta
    # para que la tabla exista cuando corra esta migración.
    dependencies = [
        ("auth", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(crear_roles, borrar_roles),
    ]
