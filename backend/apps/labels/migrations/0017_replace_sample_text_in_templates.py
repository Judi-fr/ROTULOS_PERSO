"""El editor arrancaba con textos de ejemplo que se guardaban como texto fijo: una
plantilla armada "en blanco" imprimía ese destinatario y esa dirección en cada
pedido real. Se cambian por los marcadores, solo donde el texto es exactamente el
de ejemplo: lo que alguien escribió a mano queda como está.

La versión actual de la plantilla cambia sin sumar otra al historial, que conserva
lo anterior. Sin miniatura: la vuelve a generar ``generate_template_thumbnails``.
"""
from django.db import migrations

SAMPLE_TEXTS = {
    "remitente": ("Remitente: Juan Pérez", "Remitente: {{remitente}}"),
    "destinatario": ("Destinatario: María Gómez", "Destinatario: {{destinatario}}"),
    "domicilio": ("Domicilio: Av. Siempre Viva 742", "Domicilio: {{domicilio}}"),
    "cp": ("CP: 1000", "CP: {{cp}}"),
    "localidad": ("Localidad/Provincia: CABA, Bs. As.", "Localidad/Provincia: {{localidad}}"),
    "pedido": ("N° de Pedido: 000123", "{{pedido}}"),
}


def replace_sample_texts(design):
    """Devuelve True si cambió algo en ``design``."""
    changed = False
    for field, (sample, marker) in SAMPLE_TEXTS.items():
        entry = design.get(field) if isinstance(design, dict) else None
        if isinstance(entry, dict) and entry.get("text") == sample:
            entry["text"] = marker
            changed = True
    return changed


def forwards(apps, schema_editor):
    LabelTemplate = apps.get_model("labels", "LabelTemplate")
    for template in LabelTemplate.objects.all():
        if replace_sample_texts(template.design):
            template.preview = None
            template.save(update_fields=["design", "preview"])


class Migration(migrations.Migration):
    dependencies = [("labels", "0016_seed_barcode_tracking_variables")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
