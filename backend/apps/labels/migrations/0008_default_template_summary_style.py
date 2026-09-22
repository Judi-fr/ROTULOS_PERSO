"""Rediseña la plantilla estándar con el estilo "resumen de pedido".

Bloques con título ("Enviar a:", "Remitente:"), separadores, negritas y borde
de corte, en 10x15 cm y con QR y código de barras. Solo muestra lo necesario
para entregar el paquete: ni productos, ni precios, ni email o teléfono del
comprador, porque el rótulo lo ve cualquiera que manipula el envío.

Solo se toca si la plantilla de sistema sigue con el diseño original (si un
admin la editó a mano, se respeta).
"""

from django.db import migrations

TEMPLATE_NAME = "Etiqueta de envío estándar"

OLD_DESIGN = {   'logo': {'left': 6, 'top': 5},
    'qr': {'left': 68, 'top': 5, 'size': 3, 'data': '{{tracking_url}}'},
    'remitente': {'left': 6, 'top': 22, 'text': 'Remitente: {{remitente}}'},
    'destinatario': {'left': 6, 'top': 38, 'text': 'Destinatario: {{destinatario}}'},
    'domicilio': {'left': 6, 'top': 47, 'text': 'Domicilio: {{domicilio}}'},
    'cp': {'left': 6, 'top': 56, 'text': 'CP: {{cp}}'},
    'localidad': {'left': 6, 'top': 63, 'text': '{{localidad}}'},
    'pedido': {'left': 6, 'top': 72, 'text': '{{pedido}}'},
    'barcode': {   'left': 6,
                   'top': 82,
                   'width': 8,
                   'height': 1.5,
                   'symbology': 'code128',
                   'data': '{{tracking}}',
                   'show_text': True}}

NEW_DESIGN = {   'border': {'dashed': True},
    'lines': [{'top': 14}, {'top': 47}, {'top': 60}],
    'pedido': {   'left': 6,
                  'top': 4,
                  'text': '{{pedido}}',
                  'font_size': 16,
                  'bold': True,
                  'width': 88,
                  'shrink_to_fit': True},
    'texts': [   {   'left': 6,
                     'top': 9.4,
                     'text': 'Realizado el {{fecha_pedido}}',
                     'font_size': 8.5,
                     'hide_if_empty': True},
                 {'left': 6, 'top': 16, 'text': 'Enviar a:', 'font_size': 8.5},
                 {   'left': 6,
                     'top': 29.3,
                     'text': '{{referencia}}',
                     'font_size': 9.5,
                     'width': 88,
                     'hide_if_empty': True,
                     'shrink_to_fit': True},
                 {   'left': 6,
                     'top': 39.7,
                     'text': '{{pais}}',
                     'font_size': 10,
                     'width': 88,
                     'hide_if_empty': True},
                 {   'left': 6,
                     'top': 43.2,
                     'text': 'Envío: {{envio}}',
                     'font_size': 9.5,
                     'width': 88,
                     'hide_if_empty': True,
                     'shrink_to_fit': True},
                 {'left': 6, 'top': 49.5, 'text': 'Remitente:', 'font_size': 8.5}],
    'destinatario': {   'left': 6,
                        'top': 19.5,
                        'text': '{{destinatario}}',
                        'font_size': 14,
                        'bold': True,
                        'width': 88,
                        'shrink_to_fit': True},
    'domicilio': {   'left': 6,
                     'top': 25.3,
                     'text': '{{domicilio}}',
                     'font_size': 11,
                     'width': 88,
                     'shrink_to_fit': True},
    'localidad': {   'left': 6,
                     'top': 32.8,
                     'text': '{{localidad}}',
                     'font_size': 11,
                     'width': 88,
                     'shrink_to_fit': True},
    'cp': {   'left': 6,
              'top': 36.3,
              'text': 'CP {{cp}}',
              'font_size': 11,
              'bold': True,
              'width': 88,
              'hide_if_empty': True},
    'remitente': {   'left': 6,
                     'top': 53,
                     'text': '{{remitente}}',
                     'font_size': 13,
                     'bold': True,
                     'width': 88,
                     'shrink_to_fit': True},
    'barcode': {   'left': 6,
                   'top': 64,
                   'width': 5.8,
                   'height': 2.2,
                   'symbology': 'code128',
                   'data': '{{tracking}}',
                   'show_text': True},
    'qr': {'left': 68, 'top': 63, 'size': 2.6, 'data': '{{tracking_url}}'}}


def apply_new_design(apps, schema_editor):
    LabelTemplate = apps.get_model("labels", "LabelTemplate")
    for template in LabelTemplate.objects.filter(owner=None, name=TEMPLATE_NAME):
        if template.design == OLD_DESIGN:
            template.design = NEW_DESIGN
            template.save(update_fields=["design"])


def restore_old_design(apps, schema_editor):
    LabelTemplate = apps.get_model("labels", "LabelTemplate")
    for template in LabelTemplate.objects.filter(owner=None, name=TEMPLATE_NAME):
        if template.design == NEW_DESIGN:
            template.design = OLD_DESIGN
            template.save(update_fields=["design"])


class Migration(migrations.Migration):

    dependencies = [
        ("labels", "0007_rename_default_label_template"),
    ]

    operations = [migrations.RunPython(apply_new_design, restore_old_design)]
