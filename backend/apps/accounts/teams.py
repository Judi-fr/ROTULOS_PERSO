"""Comercios con equipo: la ÚNICA forma de preguntar de qué comercio es un
usuario, qué puede hacer en él y qué tiendas ve.

Los datos del comercio cuelgan del usuario dueño. Cada vista que lee o crea
datos usa ``data_owner(request.user)`` en vez de ``request.user``; el actor
de la auditoría y los campos de autoría siguen siendo la persona real.
Diseño: ``docs/superpowers/specs/2026-10-19-equipos-design.md``.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q

from .team_models import Team, TeamMember, TeamProfile

# Los accesos que el dueño reparte, en el idioma del comerciante.
ACCESSES = {
    "orders.view": "Ver pedidos",
    "labels.print": "Imprimir rótulos",
    "orders.dispatch": "Despachar",
    "orders.cancel": "Cancelar pedidos",
    "orders.import": "Cargar e importar pedidos",
    "templates": "Plantillas de rótulos",
    "stores": "Tiendas",
    "carriers": "Transportistas y tarifas",
    "printers": "Impresoras",
    "account": "Datos del comercio",
    "team": "Equipo",
    "orders.export": "Exportar",
}

# Con los que arranca cada comercio; el dueño los cambia.
SUGGESTED_PROFILES = {
    "Todo": list(ACCESSES),
    "Depósito": ["orders.view", "labels.print", "orders.dispatch"],
    "Atención": ["orders.view", "orders.import", "orders.cancel"],
}


def create_team(owner) -> Team:
    """Comercio nuevo con ``owner`` como dueño y los perfiles sugeridos."""
    with transaction.atomic():
        team = Team.objects.create(owner=owner)
        TeamMember.objects.create(team=team, user=owner, role=TeamMember.Role.OWNER)
        TeamProfile.objects.bulk_create(
            TeamProfile(team=team, name=name, accesses=list(accesses))
            for name, accesses in SUGGESTED_PROFILES.items()
        )
    return team


def membership(user) -> TeamMember:
    """La membresía del usuario; si no tiene, le crea su comercio propio.

    Es la red para los usuarios nuevos (registro, Google, alta del admin):
    el proyecto no usa señales, así que el comercio se crea la primera vez
    que se pregunta por él.
    """
    try:
        return TeamMember.objects.select_related("team__owner", "profile").get(user=user)
    except TeamMember.DoesNotExist:
        create_team(user)
        return TeamMember.objects.select_related("team__owner", "profile").get(user=user)


def team_for(user) -> Team:
    return membership(user).team


def data_owner(user):
    """El usuario del que cuelgan los datos del comercio de ``user``."""
    return team_for(user).owner


def member_accesses(user) -> set[str]:
    """Los accesos del usuario en su comercio: el dueño, todos; un empleado,
    los de su perfil o (personalizado) los suyos."""
    member = membership(user)
    if member.role == TeamMember.Role.OWNER:
        return set(ACCESSES)
    chosen = member.profile.accesses if member.profile_id else member.accesses
    return {access for access in chosen if access in ACCESSES}


def can_access(user, access: str) -> bool:
    return access in member_accesses(user)


# --- Tiendas por empleado ---------------------------------------------------
# Un empleado puede estar limitado a algunas tiendas del comercio: ve sus
# pedidos y los cargados a mano (que no son de ninguna tienda), y nada más.
# Plantillas, transportistas e impresoras son del comercio entero.


def allowed_store_ids(user) -> set[int] | None:
    """Ids de las tiendas que ve ``user``; ``None`` = todas (dueño, o
    empleado con "todas las tiendas")."""
    member = membership(user)
    if member.role == TeamMember.Role.OWNER or member.all_stores:
        return None
    return set(member.stores.values_list("id", flat=True))


def order_scope_q(user, prefix="") -> Q:
    """Filtro de pedidos visibles. ``prefix`` para llegar al pedido desde
    otro modelo (``"order__"`` en un envío)."""
    allowed = allowed_store_ids(user)
    if allowed is None:
        return Q()
    return Q(**{f"{prefix}store_connection__isnull": True}) | Q(**{f"{prefix}store_connection_id__in": allowed})


def store_scope_q(user, field="id") -> Q:
    """Filtro de tiendas visibles (``field``: el id de la tienda en ese modelo)."""
    allowed = allowed_store_ids(user)
    if allowed is None:
        return Q()
    return Q(**{f"{field}__in": allowed})


def orders_outside_scope(user, orders) -> list:
    """Los pedidos de la lista que ``user`` no puede ver por su tienda."""
    allowed = allowed_store_ids(user)
    if allowed is None:
        return []
    return [order for order in orders if order.store_connection_id and order.store_connection_id not in allowed]


OUTSIDE_SCOPE_MESSAGE = "Hay pedidos de tiendas que no tenés habilitadas."


def visible_document_ids(user, queryset):
    """Ids de los documentos de ``queryset`` que ``user`` puede ver: los que
    no incluyen pedidos de tiendas que no tiene (``Document.store_ids``)."""
    allowed = allowed_store_ids(user)
    if allowed is None:
        return None
    return [
        doc_id
        for doc_id, store_ids in queryset.values_list("id", "store_ids")
        if store_ids is not None and set(store_ids) <= allowed
    ]
