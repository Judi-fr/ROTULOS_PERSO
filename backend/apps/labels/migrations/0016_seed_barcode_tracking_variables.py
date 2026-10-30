"""Código de barras y número de seguimiento en el catálogo de variables
(2026-10-30, foto → plantilla).

Casi todo rótulo de envío tiene un código de barras con el número de
seguimiento y ese número escrito debajo, pero el catálogo no los tenía: la
lectura de la foto los dejaba afuera o los confundía con el número de pedido.
Las descripciones las lee Claude (``apps.processing.schema``), por eso dicen
cómo distinguirlos.

Idempotente (get_or_create por código), como 0006.
"""

from django.db import migrations

VARIABLES = [
    {
        "code": "codigo_barras",
        "label": "Código de barras",
        "data_type": "codigo_barras",
        "description": (
            "Código de barras lineal (barras verticales) del envío. Suele "
            "codificar el número de seguimiento del transportista y tener ese "
            "número impreso debajo. No confundir con un código QR (cuadrado)."
        ),
        "order": 90,
    },
    {
        "code": "numero_seguimiento",
        "label": "Número de seguimiento",
        "data_type": "texto",
        "description": (
            "Número de seguimiento o de envío que asigna el transportista, "
            "escrito en texto (a menudo debajo o al lado del código de barras, "
            "o precedido por 'Seguimiento', 'Tracking' o 'N° de envío'). No "
            "confundir con el número de pedido de la tienda."
        ),
        "order": 100,
    },
]


def create_variables(apps, schema_editor):
    LayoutVariable = apps.get_model("labels", "LayoutVariable")
    for variable in VARIABLES:
        LayoutVariable.objects.get_or_create(
            code=variable["code"],
            defaults={
                "label": variable["label"],
                "description": variable["description"],
                "data_type": variable["data_type"],
                "order": variable["order"],
                "is_system": True,
                "is_active": True,
            },
        )


def delete_variables(apps, schema_editor):
    LayoutVariable = apps.get_model("labels", "LayoutVariable")
    LayoutElement = apps.get_model("labels", "LayoutElement")
    codes = [variable["code"] for variable in VARIABLES]
    used = set(LayoutElement.objects.filter(variable__code__in=codes).values_list("variable__code", flat=True))
    LayoutVariable.objects.filter(code__in=[code for code in codes if code not in used], is_system=True).delete()


class Migration(migrations.Migration):

    dependencies = [("labels", "0015_gallery_templates")]

    operations = [migrations.RunPython(create_variables, delete_variables)]
