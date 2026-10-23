"""Ayudas de test para los comercios (``apps.accounts.teams``).

TRANSITORIO (el usuario pidió reescribirlos "de verdad" más adelante): desde
que una persona arranca sin comercio y los datos cuelgan del usuario de datos
de cada comercio, los tests escritos antes (que cuelgan los datos de la
persona y llaman a la API como ella) le dan a cada usuario un comercio "solo"
cuyo usuario de datos es la misma persona: los datos que ya creaban siguen
siendo de su comercio. En producción esa forma no existe.
"""

from django.contrib.auth import get_user_model

from apps.accounts.team_models import TeamMember
from apps.accounts.teams import create_team


def give_solo_team(user, name=""):
    """Un comercio con ``user`` como dueño y como usuario de datos."""
    return create_team(user, name=name, data_user=user)


def give_solo_teams():
    """``give_solo_team`` para cada usuario que todavía no está en ningún
    comercio (la línea que se agrega al final de cada setUp)."""
    with_team = TeamMember.objects.values_list("user_id", flat=True)
    for user in get_user_model().objects.exclude(pk__in=with_team).exclude(data_team__isnull=False):
        give_solo_team(user)
