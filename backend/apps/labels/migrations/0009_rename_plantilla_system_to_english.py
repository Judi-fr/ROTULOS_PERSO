from django.conf import settings
import django.core.validators
from decimal import Decimal
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Traduce a inglés el sistema de plantillas por elementos:
    Plantilla -> ElementLayout, ElementoPlantilla -> LayoutElement,
    VariableRotulo -> LayoutVariable, TipoDato -> LayoutVariableType,
    TipoElemento -> LayoutElementType, y sus campos.

    Solo renombres (RenameModel/RenameField/AlterField/AlterModelOptions):
    conserva las tablas y las filas existentes. Los VALORES de choices
    (TextChoices) no cambian, ni las claves de los JSONField `style`/
    `metadata` (siguen en español a propósito, ver apps/labels/styles.py).
    """

    dependencies = [
        ('labels', '0008_default_template_summary_style'),
    ]

    operations = [
        migrations.RenameModel(old_name='VariableRotulo', new_name='LayoutVariable'),
        migrations.RenameModel(old_name='Plantilla', new_name='ElementLayout'),
        migrations.RenameModel(old_name='ElementoPlantilla', new_name='LayoutElement'),

        # --- LayoutVariable ---
        migrations.RenameField(model_name='layoutvariable', old_name='codigo', new_name='code'),
        migrations.RenameField(model_name='layoutvariable', old_name='etiqueta', new_name='label'),
        migrations.RenameField(model_name='layoutvariable', old_name='descripcion', new_name='description'),
        migrations.RenameField(model_name='layoutvariable', old_name='tipo_dato', new_name='data_type'),
        migrations.RenameField(model_name='layoutvariable', old_name='activa', new_name='is_active'),
        migrations.RenameField(model_name='layoutvariable', old_name='es_sistema', new_name='is_system'),
        migrations.RenameField(model_name='layoutvariable', old_name='orden', new_name='order'),
        migrations.RenameField(model_name='layoutvariable', old_name='creada_por', new_name='created_by'),
        migrations.RenameField(model_name='layoutvariable', old_name='creada_en', new_name='created_at'),

        # --- ElementLayout ---
        migrations.RenameField(model_name='elementlayout', old_name='nombre', new_name='name'),
        migrations.RenameField(model_name='elementlayout', old_name='descripcion', new_name='description'),
        migrations.RenameField(model_name='elementlayout', old_name='ancho_mm', new_name='width_mm'),
        migrations.RenameField(model_name='elementlayout', old_name='alto_mm', new_name='height_mm'),
        migrations.RenameField(model_name='elementlayout', old_name='orientacion', new_name='orientation'),
        migrations.RenameField(model_name='elementlayout', old_name='metadatos', new_name='metadata'),
        migrations.RenameField(model_name='elementlayout', old_name='activa', new_name='is_active'),
        migrations.RenameField(model_name='elementlayout', old_name='creada_por', new_name='created_by'),
        migrations.RenameField(model_name='elementlayout', old_name='creada_en', new_name='created_at'),
        migrations.RenameField(model_name='elementlayout', old_name='actualizada_en', new_name='updated_at'),

        # --- LayoutElement ---
        migrations.RenameField(model_name='layoutelement', old_name='plantilla', new_name='layout'),
        migrations.RenameField(model_name='layoutelement', old_name='tipo', new_name='element_type'),
        migrations.RenameField(model_name='layoutelement', old_name='contenido', new_name='content'),
        migrations.RenameField(model_name='layoutelement', old_name='ancho_mm', new_name='width_mm'),
        migrations.RenameField(model_name='layoutelement', old_name='alto_mm', new_name='height_mm'),
        migrations.RenameField(model_name='layoutelement', old_name='estilo', new_name='style'),
        migrations.RenameField(model_name='layoutelement', old_name='orden', new_name='order'),

        # related_name changes (no cambian columnas, solo el accessor inverso)
        migrations.AlterField(
            model_name='layoutvariable',
            name='created_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='created_layout_variables',
                to=settings.AUTH_USER_MODEL, verbose_name='creada por',
            ),
        ),
        migrations.AlterField(
            model_name='elementlayout',
            name='created_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='created_element_layouts',
                to=settings.AUTH_USER_MODEL, verbose_name='creada por',
            ),
        ),
        migrations.AlterField(
            model_name='layoutelement',
            name='layout',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='elements',
                to='labels.elementlayout', verbose_name='plantilla',
            ),
        ),
        migrations.AlterField(
            model_name='layoutelement',
            name='variable',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='elements',
                to='labels.layoutvariable', verbose_name='variable',
            ),
        ),

        # Meta.ordering referenciaba los campos viejos.
        migrations.AlterModelOptions(
            name='layoutvariable',
            options={
                'verbose_name': 'variable de rótulo',
                'verbose_name_plural': 'variables de rótulo',
                'ordering': ['order', 'label'],
            },
        ),
        migrations.AlterModelOptions(
            name='elementlayout',
            options={
                'verbose_name': 'plantilla',
                'verbose_name_plural': 'plantillas',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AlterModelOptions(
            name='layoutelement',
            options={
                'verbose_name': 'elemento de plantilla',
                'verbose_name_plural': 'elementos de plantilla',
                'ordering': ['order', 'id'],
            },
        ),

        # El CheckConstraint referenciaba tipo/contenido -> renombrado junto
        # con los campos (mismo nombre de constraint no hace falta cambiarlo,
        # pero se aprovecha para que el nombre en la DB también diga qué es).
        migrations.RemoveConstraint(
            model_name='layoutelement',
            name='labels_elemento_coherente_con_tipo',
        ),
        migrations.AddConstraint(
            model_name='layoutelement',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(element_type='variable', variable__isnull=False, content='')
                    | (
                        models.Q(element_type='texto_estatico', variable__isnull=True)
                        & ~models.Q(content='')
                    )
                    | models.Q(
                        element_type__in=['linea', 'recuadro'],
                        variable__isnull=True,
                        content='',
                    )
                ),
                name='labels_element_coherent_with_type',
            ),
        ),
    ]
