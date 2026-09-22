"""Quita "Buspack" de la plantilla pública sembrada por 0003.

La app es multi-cliente: la plantilla de sistema la ven todos los clientes,
así que no puede llevar el nombre de uno. Solo se renombra si sigue con el
nombre y la descripción originales (si un admin la editó a mano, no se toca).
"""

from django.db import migrations

OLD_NAME = "Etiqueta de encomienda Buspack"
OLD_DESCRIPTION = "Plantilla estándar de Buspack para encomiendas."
NEW_NAME = "Etiqueta de envío estándar"
NEW_DESCRIPTION = "Plantilla estándar para envíos (10x15 cm)."


def rename(apps, schema_editor):
    LabelTemplate = apps.get_model("labels", "LabelTemplate")
    LabelTemplate.objects.filter(owner=None, name=OLD_NAME, description=OLD_DESCRIPTION).update(
        name=NEW_NAME, description=NEW_DESCRIPTION
    )


def unrename(apps, schema_editor):
    LabelTemplate = apps.get_model("labels", "LabelTemplate")
    LabelTemplate.objects.filter(owner=None, name=NEW_NAME, description=NEW_DESCRIPTION).update(
        name=OLD_NAME, description=OLD_DESCRIPTION
    )


class Migration(migrations.Migration):

    dependencies = [
        ("labels", "0006_seed_variables_sistema"),
    ]

    operations = [migrations.RunPython(rename, unrename)]
