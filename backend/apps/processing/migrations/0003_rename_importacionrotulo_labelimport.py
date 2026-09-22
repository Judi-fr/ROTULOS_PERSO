from django.conf import settings
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Renombra ImportacionRotulo -> LabelImport (y sus campos a inglés).

    Solo renombres (RenameModel/RenameField/AlterField/AlterModelOptions):
    conserva la tabla y las filas existentes. Los valores de EstadoImportacion
    (ahora LabelImportStatus) no cambian; el contenido de `proposal`/
    `raw_response` tampoco se toca acá (son JSONField, no columnas).
    """

    dependencies = [
        ('processing', '0002_alter_importacionrotulo_documento'),
    ]

    operations = [
        migrations.RenameModel(old_name='ImportacionRotulo', new_name='LabelImport'),

        migrations.RenameField(model_name='labelimport', old_name='documento', new_name='uploaded_file'),
        migrations.RenameField(model_name='labelimport', old_name='estado', new_name='status'),
        migrations.RenameField(model_name='labelimport', old_name='modelo', new_name='model_name'),
        migrations.RenameField(model_name='labelimport', old_name='propuesta', new_name='proposal'),
        migrations.RenameField(model_name='labelimport', old_name='respuesta_cruda', new_name='raw_response'),
        migrations.RenameField(model_name='labelimport', old_name='tokens_entrada', new_name='input_tokens'),
        migrations.RenameField(model_name='labelimport', old_name='tokens_salida', new_name='output_tokens'),
        migrations.RenameField(model_name='labelimport', old_name='creada_por', new_name='created_by'),
        migrations.RenameField(model_name='labelimport', old_name='creada_en', new_name='created_at'),
        migrations.RenameField(model_name='labelimport', old_name='finalizada_en', new_name='finished_at'),

        migrations.AlterField(
            model_name='labelimport',
            name='uploaded_file',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='label_imports',
                to='documents.uploadedlabelfile', verbose_name='documento',
            ),
        ),
        migrations.AlterField(
            model_name='labelimport',
            name='created_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='label_imports',
                to=settings.AUTH_USER_MODEL, verbose_name='creada por',
            ),
        ),

        migrations.AlterModelOptions(
            name='labelimport',
            options={
                'verbose_name': 'importación de rótulo',
                'verbose_name_plural': 'importaciones de rótulo',
                'ordering': ['-created_at'],
            },
        ),
    ]
