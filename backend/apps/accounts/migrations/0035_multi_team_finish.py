"""Varios comercios por persona (etapa 1), parte 3: cada comercio tiene ya su
usuario de datos y su código; ``former_owner`` deja de hacer falta (en un
comercio dado de baja ``owner`` sigue siendo la persona)."""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0034_multi_team_data"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="team",
            name="data_user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="data_team",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="team",
            name="join_code",
            field=models.CharField(max_length=20, unique=True, verbose_name="código para sumarse"),
        ),
        migrations.RemoveField(
            model_name="team",
            name="former_owner",
        ),
    ]
