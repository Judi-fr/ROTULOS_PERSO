"""Ayudas de test para los comercios (``apps.accounts.teams``).

Como en producción: una persona arranca sin comercio, y cuando tiene uno los
datos del comercio (pedidos, tiendas, plantillas, cuentas de transportista...)
cuelgan de su usuario de datos interno (``Team.data_user``), nunca de la
persona. Un test crea el comercio ANTES de crear esos datos y los cuelga del
usuario de datos que devuelve ``new_business``; la persona sigue siendo quien
llama a la API, el actor de la auditoría y la autora (``created_by``...).
"""

from django.contrib.auth import get_user_model

from apps.accounts.team_models import TeamMember
from apps.accounts.teams import create_team


def new_business(owner, name=""):
    """Comercio nuevo con ``owner`` de dueño; devuelve su usuario de datos."""
    return create_team(owner, name=name).data_user


def give_teams():
    """Un comercio para cada persona que todavía no está en ninguno (los
    usuarios de datos no son personas y se saltean)."""
    with_team = TeamMember.objects.values_list("user_id", flat=True)
    people = get_user_model().objects.exclude(pk__in=with_team).exclude(data_team__isnull=False)
    for user in people:
        create_team(user)
