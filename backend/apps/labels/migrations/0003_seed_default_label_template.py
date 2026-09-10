"""Siembra la plantilla pública por defecto de Buspack.

Sin esto la tabla de plantillas queda vacía y el lote (``POST
/api/v1/labels/batch/`` por ``order_ids``/``filters``) es imposible de usar:
exige una plantilla y no hay ninguna que ofrecer (ver
``batch_views.LabelBatchView._resolve_template``, que ahora cae a la
primera plantilla pública activa cuando no se manda ``template_id``).

Diseño estándar 10x15 cm: logo arriba a la izquierda, QR arriba a la
derecha (marcador ``{{tracking_url}}``), campos de texto con marcadores
(``{{remitente}}``, ``{{destinatario}}``, ...) y el código de barras abajo
con ``{{tracking}}`` — mismo formato que valida
``apps.labels.serializers.validate_design`` y dibuja
``apps.labels.rendering.draw_label_page``.

Idempotente: ``get_or_create`` por nombre + ``owner=None`` (una plantilla
de sistema no tiene otra forma más natural de identificarse). Si ya existe
no se toca (un admin pudo haberla editado a mano).
"""

from django.db import migrations

TEMPLATE_NAME = "Etiqueta de encomienda Buspack"

DEFAULT_DESIGN = {
    "logo": {"left": 6, "top": 5},
    "qr": {"left": 68, "top": 5, "size": 3, "data": "{{tracking_url}}"},
    "remitente": {"left": 6, "top": 22, "text": "Remitente: {{remitente}}"},
    "destinatario": {"left": 6, "top": 38, "text": "Destinatario: {{destinatario}}"},
    "domicilio": {"left": 6, "top": 47, "text": "Domicilio: {{domicilio}}"},
    "cp": {"left": 6, "top": 56, "text": "CP: {{cp}}"},
    "localidad": {"left": 6, "top": 63, "text": "{{localidad}}"},
    "pedido": {"left": 6, "top": 72, "text": "{{pedido}}"},
    "barcode": {
        "left": 6,
        "top": 82,
        "width": 8,
        "height": 1.5,
        "symbology": "code128",
        "data": "{{tracking}}",
        "show_text": True,
    },
}


def seed_default_template(apps, schema_editor):
    LabelTemplate = apps.get_model("labels", "LabelTemplate")
    LabelTemplate.objects.get_or_create(
        name=TEMPLATE_NAME,
        owner=None,
        defaults={
            "description": "Plantilla estándar de Buspack para encomiendas.",
            "is_public": True,
            "is_active": True,
            "width_cm": 10,
            "height_cm": 15,
            "design": DEFAULT_DESIGN,
        },
    )


def unseed_default_template(apps, schema_editor):
    LabelTemplate = apps.get_model("labels", "LabelTemplate")
    LabelTemplate.objects.filter(name=TEMPLATE_NAME, owner=None).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("labels", "0002_labeltemplate_is_active"),
    ]

    operations = [
        migrations.RunPython(seed_default_template, unseed_default_template),
    ]
