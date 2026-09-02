"""Siembra el catálogo inicial de variables de rótulo.

Son las mismas ocho que hasta ahora vivían como enum en ``models.py``. Se
marcan con ``es_sistema`` para que ni la API ni el admin las dejen borrar: hay
plantillas que las usan y el motor de impresión espera sus códigos.

Las descripciones no son documentación interna: las lee el servicio que
importa rótulos desde una foto para decidir a qué variable corresponde cada
dato detectado. De ahí que remitente y destinatario aclaren en mayúsculas quién
envía y quién recibe, que es la confusión más fácil de cometer.

Idempotente: usa get_or_create, así reaplicarla no duplica ni pisa cambios que
alguien haya hecho después desde el admin.
"""

from django.db import migrations

VARIABLES = [
    {
        "codigo": "qr",
        "etiqueta": "QR",
        "tipo_dato": "qr",
        "descripcion": (
            "Código QR del envío. Suele ocupar un cuadrado en una esquina del "
            "rótulo y codifica el número de seguimiento."
        ),
        "orden": 10,
    },
    {
        "codigo": "logo_empresa",
        "etiqueta": "Logo de empresa",
        "tipo_dato": "imagen",
        "descripcion": (
            "Isologo de la empresa que envía, normalmente en el encabezado."
        ),
        "orden": 20,
    },
    {
        "codigo": "remitente",
        "etiqueta": "Remitente",
        "tipo_dato": "texto",
        "descripcion": (
            "Nombre de la persona o empresa que ENVÍA el paquete. No "
            "confundir con el destinatario."
        ),
        "orden": 30,
    },
    {
        "codigo": "destinatario",
        "etiqueta": "Destinatario",
        "tipo_dato": "texto",
        "descripcion": (
            "Nombre de la persona o empresa que RECIBE el paquete. No "
            "confundir con el remitente."
        ),
        "orden": 40,
    },
    {
        "codigo": "domicilio",
        "etiqueta": "Domicilio",
        "tipo_dato": "texto",
        "descripcion": (
            "Calle y número de la dirección de entrega, con piso y "
            "departamento si corresponde."
        ),
        "orden": 50,
    },
    {
        "codigo": "codigo_postal",
        "etiqueta": "Código Postal",
        "tipo_dato": "texto",
        "descripcion": (
            "Código postal del domicilio de entrega. Puede aparecer "
            "precedido por CP o C.P."
        ),
        "orden": 60,
    },
    {
        "codigo": "localidad_provincia",
        "etiqueta": "Localidad/Provincia",
        "tipo_dato": "texto",
        "descripcion": "Localidad y provincia del domicilio de entrega.",
        "orden": 70,
    },
    {
        "codigo": "numero_pedido",
        "etiqueta": "Número de Pedido",
        "tipo_dato": "texto",
        "descripcion": (
            "Identificador del pedido u orden de compra asociada al envío."
        ),
        "orden": 80,
    },
]


def crear_variables(apps, schema_editor):
    VariableRotulo = apps.get_model("labels", "VariableRotulo")
    for variable in VARIABLES:
        VariableRotulo.objects.get_or_create(
            codigo=variable["codigo"],
            defaults={
                "etiqueta": variable["etiqueta"],
                "descripcion": variable["descripcion"],
                "tipo_dato": variable["tipo_dato"],
                "orden": variable["orden"],
                "es_sistema": True,
                "activa": True,
            },
        )


def borrar_variables(apps, schema_editor):
    # Si alguna está colocada en una plantilla, el PROTECT del FK aborta la
    # marcha atrás. Es lo correcto: revertir no debería vaciar plantillas.
    VariableRotulo = apps.get_model("labels", "VariableRotulo")
    VariableRotulo.objects.filter(
        codigo__in=[v["codigo"] for v in VARIABLES], es_sistema=True
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("labels", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(crear_variables, borrar_variables),
    ]
