"""Un comercio propio para cada usuario que ya existe (admins incluidos),
con él como dueño y los perfiles sugeridos. Replica teams.create_team con los
modelos históricos. Para un comerciante solo, nada cambia: sus datos ya
cuelgan de él, que pasa a ser el dueño."""

from django.db import migrations

ACCESSES = [
    "orders.view", "labels.print", "orders.dispatch", "orders.cancel", "orders.import", "templates",
    "stores", "carriers", "printers", "account", "team", "orders.export",
]
SUGGESTED_PROFILES = {
    "Todo": ACCESSES,
    "Depósito": ["orders.view", "labels.print", "orders.dispatch"],
    "Atención": ["orders.view", "orders.import", "orders.cancel"],
}


def create_teams(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Team = apps.get_model("accounts", "Team")
    TeamMember = apps.get_model("accounts", "TeamMember")
    TeamProfile = apps.get_model("accounts", "TeamProfile")
    with_team = set(TeamMember.objects.values_list("user_id", flat=True))
    for user in User.objects.exclude(id__in=with_team).iterator():
        team = Team.objects.create(owner=user)
        TeamMember.objects.create(team=team, user=user, role="owner")
        TeamProfile.objects.bulk_create(
            TeamProfile(team=team, name=name, accesses=list(accesses)) for name, accesses in SUGGESTED_PROFILES.items()
        )


def remove_teams(apps, schema_editor):
    apps.get_model("accounts", "TeamMember").objects.all().delete()
    apps.get_model("accounts", "Team").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0026_teams"),
    ]

    operations = [
        migrations.RunPython(create_teams, remove_teams),
    ]
