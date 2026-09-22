import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Renombra Documento -> UploadedLabelFile (y sus campos a inglés).

    Solo renombres (RenameModel/RenameField/AlterField/AlterModelOptions):
    conserva la tabla y las filas existentes, no borra y recrea.
    """

    dependencies = [
        ('documents', '0002_documento'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        # processing.0001_initial crea un FK a 'documents.documento' (el
        # nombre viejo): sin esta dependencia explícita, el grafo de
        # migraciones puede ordenar este RenameModel ANTES de esa
        # CreateModel al rearmar la base desde cero (como hace `test`),
        # y falla con "Related model 'documents.documento' cannot be
        # resolved".
        ('processing', '0001_initial'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='Documento',
            new_name='UploadedLabelFile',
        ),
        migrations.RenameField(
            model_name='uploadedlabelfile',
            old_name='archivo',
            new_name='file',
        ),
        migrations.RenameField(
            model_name='uploadedlabelfile',
            old_name='nombre_original',
            new_name='original_filename',
        ),
        migrations.RenameField(
            model_name='uploadedlabelfile',
            old_name='tipo_mime',
            new_name='mime_type',
        ),
        migrations.RenameField(
            model_name='uploadedlabelfile',
            old_name='tamano_bytes',
            new_name='size_bytes',
        ),
        migrations.RenameField(
            model_name='uploadedlabelfile',
            old_name='subido_por',
            new_name='uploaded_by',
        ),
        migrations.RenameField(
            model_name='uploadedlabelfile',
            old_name='subido_en',
            new_name='uploaded_at',
        ),
        migrations.AlterField(
            model_name='uploadedlabelfile',
            name='uploaded_by',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='uploaded_label_files',
                to=settings.AUTH_USER_MODEL,
                verbose_name='subido por',
            ),
        ),
        migrations.AlterModelOptions(
            name='uploadedlabelfile',
            options={
                'verbose_name': 'archivo subido',
                'verbose_name_plural': 'archivos subidos',
                'ordering': ['-uploaded_at'],
            },
        ),
    ]
