"""Varios comercios por persona (etapa 1), parte 2: los datos.

Hasta acá los datos de cada comercio colgaban de la persona dueña. Desde acá
cuelgan del usuario de datos del comercio (``Team.data_user``), porque una
persona puede ser dueña de varios. Por cada comercio:

- dado de baja: su usuario archivo (que era el ``owner``) pasa a ser su
  usuario de datos, y ``owner`` vuelve a ser la persona (``former_owner``);
- abierto y VACÍO (ningún dato y nadie más que el dueño): se borra; esa
  persona va a ver la pantalla para crear un comercio o sumarse a uno;
- abierto con datos o con empleados: se le crea su usuario de datos y se le
  mueven los datos del dueño (como hacía ``transfer_ownership``).

Todos los que quedan reciben su código para sumarse. No se deshace: no hay
forma segura de volver a mezclar los datos en una persona.
"""

import secrets

from django.contrib.auth.hashers import make_password
from django.db import migrations

# team_services.OWNED_DATA cuando se escribió esta migración.
OWNED_DATA = [
    ("integrations", "StoreConnection", "owner"),
    ("orders", "Order", "user"),
    ("orders", "Address", "user"),
    ("orders", "OrderImport", "user"),
    ("orders", "ImportMapping", "user"),
    ("labels", "LabelTemplate", "owner"),
    ("labels", "Label", "user"),
    ("labels", "LabelSequence", "owner"),
    ("documents", "Document", "user"),
    ("carriers", "CarrierAccount", "owner"),
    ("printing", "PrintAgent", "owner"),
    ("printing", "Printer", "owner"),
    ("printing", "PrintJob", "owner"),
    ("integrations", "IntegrationKey", "owner"),
    ("integrations", "IncomingWebhook", "owner"),
    ("integrations", "WebhookEndpoint", "owner"),
    ("accounts", "AccountSettings", "user"),
]
JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _models(apps):
    return [(apps.get_model(app, name), field) for app, name, field in OWNED_DATA]


def _join_code(Team, taken):
    while True:
        code = "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(10))
        if code not in taken and not Team.objects.filter(join_code=code).exists():
            taken.add(code)
            return code


def forwards(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Team = apps.get_model("accounts", "Team")
    TeamMember = apps.get_model("accounts", "TeamMember")
    MerchantNotice = apps.get_model("monitoring", "MerchantNotice")
    owned = _models(apps)
    taken = set()

    for team in Team.objects.all().order_by("pk"):
        if team.closed_at is not None:
            archive_id = team.owner_id
            TeamMember.objects.filter(team=team, user_id=archive_id).delete()
            team.data_user_id = archive_id
            team.owner_id = team.former_owner_id
            team.join_code = _join_code(Team, taken)
            team.join_code_enabled = False
            team.save()
            continue

        owner_id = team.owner_id
        has_data = any(model.objects.filter(**{field + "_id": owner_id}).exists() for model, field in owned)
        has_others = TeamMember.objects.filter(team=team).exclude(user_id=owner_id).exists()
        if not has_data and not has_others:
            team.delete()  # miembros, perfiles e invitaciones en cascada
            continue

        email = f"comercio-{team.pk}-{secrets.token_hex(4)}@datos.invalid"
        data_user = User.objects.create(
            username=email, email=email, is_active=False, password=make_password(None)
        )
        for model, field in owned:
            model.objects.filter(**{field + "_id": owner_id}).update(**{field + "_id": data_user.pk})
        MerchantNotice.objects.filter(owner_id=owner_id).update(owner_id=data_user.pk)
        team.data_user_id = data_user.pk
        team.join_code = _join_code(Team, taken)
        team.save()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0033_multi_team_schema"),
        ("orders", "0004_address_origin"),
        ("labels", "0012_catalogs"),
        ("integrations", "0015_empretienda"),
        ("carriers", "0004_catalogs"),
        ("printing", "0001_initial"),
        ("documents", "0006_uploaded_label_random_path"),
        ("monitoring", "0002_merchant_notices"),
    ]

    operations = [
        # Sin vuelta atrás real (no hay forma segura de volver a mezclar los
        # datos en una persona): deshacerla no toca nada.
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
