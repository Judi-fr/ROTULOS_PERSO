"""Agrega el tipo de dato ``qr_envio`` y siembra su variable en el catálogo.

Es el QR que en vez de llevar un número de seguimiento suelto lleva adentro
todos los datos del envío. La diferencia práctica: un QR con "AR0087123456"
obliga a consultar un sistema para saber a quién va el paquete; uno con el
destinatario, el domicilio y el pedido se lee en el depósito aunque no haya
señal.

La descripción no es documentación interna: la lee el modelo que importa
rótulos desde una foto, y es lo que le permite distinguir este QR de uno
común. Por eso aclara que el contenido se genera solo.
"""

from django.db import migrations, models

CODIGO = "qr_envio"


def crear_variable(apps, schema_editor):
    VariableRotulo = apps.get_model("labels", "VariableRotulo")
    VariableRotulo.objects.get_or_create(
        codigo=CODIGO,
        defaults={
            "etiqueta": "QR del envío completo",
            "descripcion": (
                "Código QR que contiene todos los datos del envío (destinatario, "
                "domicilio, remitente, pedido) codificados como JSON. A "
                "diferencia de la variable `qr`, su contenido no se carga a "
                "mano: lo arma el sistema con el resto de los campos del "
                "rótulo. Suele ocupar un cuadrado grande, porque cuantos más "
                "datos lleva más denso queda."
            ),
            "tipo_dato": "qr_envio",
            "orden": 15,  # junto al `qr` común, que está en 10
            "es_sistema": True,
            "activa": True,
        },
    )


def borrar_variable(apps, schema_editor):
    # Si está colocada en alguna plantilla, el PROTECT del FK aborta la marcha
    # atrás. Es lo correcto: revertir no debería vaciar plantillas.
    VariableRotulo = apps.get_model("labels", "VariableRotulo")
    VariableRotulo.objects.filter(codigo=CODIGO, es_sistema=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("labels", "0002_variables_sistema"),
    ]

    operations = [
        migrations.AlterField(
            model_name="variablerotulo",
            name="tipo_dato",
            field=models.CharField(
                choices=[
                    ("texto", "Texto"),
                    ("qr", "Código QR"),
                    ("qr_envio", "Código QR con el envío completo"),
                    ("codigo_barras", "Código de barras"),
                    ("imagen", "Imagen"),
                ],
                default="texto",
                max_length=20,
                verbose_name="tipo de dato",
            ),
        ),
        migrations.RunPython(crear_variable, borrar_variable),
    ]
