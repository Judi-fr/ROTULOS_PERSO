from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("labels", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="labeltemplate",
            name="is_active",
            field=models.BooleanField(default=True),
        ),
    ]
