"""Suma domicilio y teléfono del remitente a la plantilla estándar.

Un rótulo sin los datos de quien despacha no se puede devolver si no se
entrega. Los dos textos usan ``hide_if_empty``: la tienda que no cargó su
remitente (``StoreConnection.sender_address``/``sender_phone``) imprime
exactamente lo mismo que antes, sin huecos ni etiquetas vacías.

Para hacerles lugar, el separador de abajo baja de 60 a 62 (el código de
barras y el QR no se mueven) y el nombre del remitente sube de 53 a 51.5.

Solo se toca si la plantilla de sistema sigue con el diseño de la migración
0008: si un admin la editó a mano, se respeta.
"""

from copy import deepcopy
from importlib import import_module

from django.db import migrations

# El módulo 0008 no se puede importar con `from .0008... import` (un nombre
# de módulo no puede empezar con un número).
DESIGN_0008 = import_module(
    "apps.labels.migrations.0008_default_template_summary_style"
).NEW_DESIGN

TEMPLATE_NAME = "Etiqueta de envío estándar"


def _with_sender_contact(design):
    updated = deepcopy(design)
    updated["lines"] = [{"top": 14}, {"top": 47}, {"top": 62}]
    updated["remitente"] = {**updated["remitente"], "top": 51.5, "font_size": 12}
    updated["texts"] = [
        *updated["texts"],
        {
            "left": 6,
            "top": 55.8,
            "text": "{{remitente_domicilio}}",
            "font_size": 9.5,
            "width": 88,
            "hide_if_empty": True,
            "shrink_to_fit": True,
        },
        {
            "left": 6,
            "top": 58.9,
            "text": "Tel. {{remitente_telefono}}",
            "font_size": 9.5,
            "width": 88,
            "hide_if_empty": True,
        },
    ]
    return updated


NEW_DESIGN = _with_sender_contact(DESIGN_0008)


def apply_sender_contact(apps, schema_editor):
    LabelTemplate = apps.get_model("labels", "LabelTemplate")
    for template in LabelTemplate.objects.filter(owner=None, name=TEMPLATE_NAME):
        if template.design == DESIGN_0008:
            template.design = NEW_DESIGN
            template.save(update_fields=["design"])


def restore_previous_design(apps, schema_editor):
    LabelTemplate = apps.get_model("labels", "LabelTemplate")
    for template in LabelTemplate.objects.filter(owner=None, name=TEMPLATE_NAME):
        if template.design == NEW_DESIGN:
            template.design = DESIGN_0008
            template.save(update_fields=["design"])


class Migration(migrations.Migration):

    dependencies = [
        ("labels", "0009_rename_plantilla_system_to_english"),
    ]

    operations = [
        migrations.RunPython(apply_sender_contact, restore_previous_design),
    ]
