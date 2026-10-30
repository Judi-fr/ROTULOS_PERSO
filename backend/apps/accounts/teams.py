"""Comercios con equipo: la ÚNICA forma de preguntar en qué comercio está
trabajando un usuario, qué puede hacer en él y qué tiendas ve.

Una persona puede estar en varios comercios (o en ninguno). El de cada
request lo elige el frontend con el header ``X-Team-Id`` (por pestaña); sin
header, o con uno que no es suyo, vale el último que usó. Los datos de cada
comercio cuelgan de su usuario de datos (``Team.data_user``): cada vista que
lee o crea datos usa ``data_owner(request.user)`` en vez de ``request.user``;
el actor de la auditoría y los campos de autoría siguen siendo la persona.
Diseño: ``docs/superpowers/specs/2026-10-19-equipos-design.md``.
"""

from __future__ import annotations

import contextvars
import secrets
from contextlib import contextmanager

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import APIException

from .team_models import Team, TeamMember, TeamProfile

# Los accesos que el dueño reparte, en el idioma del comerciante.
ACCESSES = {
    "orders.view": "Ver pedidos",
    "labels.print": "Imprimir rótulos",
    "documents.delete": "Borrar lotes",
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
    # Estadísticas del comercio (pestaña de Mi comercio, 2026-10-30): una por
    # bloque, para que el dueño decida quién ve cada una.
    "stats.dispatch": "Estadísticas de despacho",
    "stats.carriers": "Estadísticas de transportistas",
    "stats.buyers": "Estadísticas de compradores",
}

# Con los que arranca cada comercio; el dueño los cambia.
SUGGESTED_PROFILES = {
    "Todo": list(ACCESSES),
    "Depósito": ["orders.view", "labels.print", "orders.dispatch"],
    "Atención": ["orders.view", "orders.import", "orders.cancel"],
}

# Sin letras ni números que se confundan al dictarlo (0/O, 1/I/L).
JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
JOIN_CODE_LENGTH = 10

DATA_USER_DOMAIN = "datos.invalid"


class NoTeam(APIException):
    """La persona no está en ningún comercio: el frontend la manda a
    ``comercio_inicio.html`` cuando ve ``code: no_team``."""

    status_code = status.HTTP_403_FORBIDDEN
    default_detail = {"detail": "Primero creá un comercio o sumate a uno.", "code": "no_team"}
    default_code = "no_team"


def new_join_code() -> str:
    while True:
        code = "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH))
        if not Team.objects.filter(join_code=code).exists():
            return code


def normalize_join_code(code) -> str:
    """Lo que la persona pegó, sin espacios ni guiones y en mayúsculas (o
    una URL de ``unirse.html?code=``)."""
    text = str(code or "").strip()
    if "code=" in text:
        text = text.split("code=", 1)[1].split("&", 1)[0]
    return "".join(ch for ch in text.upper() if ch.isalnum())


def _new_data_user():
    User = get_user_model()
    email = f"comercio-{secrets.token_hex(6)}@{DATA_USER_DOMAIN}"
    user = User(username=email, email=email, is_active=False)
    user.set_unusable_password()
    user.save()
    return user


def create_team(owner, name="") -> Team:
    """Comercio nuevo con ``owner`` como dueño, su usuario de datos y los
    perfiles sugeridos."""
    with transaction.atomic():
        team = Team.objects.create(
            owner=owner,
            name=(name or "").strip()[:150],
            data_user=_new_data_user(),
            join_code=new_join_code(),
        )
        TeamMember.objects.create(team=team, user=owner, role=TeamMember.Role.OWNER, last_used_at=timezone.now())
        TeamProfile.objects.bulk_create(
            TeamProfile(team=team, name=name, accesses=list(accesses))
            for name, accesses in SUGGESTED_PROFILES.items()
        )
    forget(owner)
    return team


# Una request pregunta muchas veces por la membresía (cada permiso, cada
# data_owner): dentro de una request se lee una sola vez por usuario.
# ``TeamCacheMiddleware`` abre la caché y lee el header ``X-Team-Id``; fuera
# de una request (worker, shell, tests que llaman directo) no hay caché ni
# header y cada llamada lee la base.
_request_members = contextvars.ContextVar("team_request_members", default=None)
_requested_team = contextvars.ContextVar("team_requested_id", default=None)


@contextmanager
def request_cache(team_id=None):
    token = _request_members.set({})
    team_token = _requested_team.set(team_id)
    try:
        yield
    finally:
        _requested_team.reset(team_token)
        _request_members.reset(token)


def forget(user):
    """Olvida las membresías cacheadas de ``user`` (después de cambiarlas)."""
    cache = _request_members.get()
    if cache is not None:
        cache.pop(user.pk, None)


def use_team(team_id):
    """Desde acá, esta request trabaja en ``team_id`` (si el usuario es
    miembro): cambiar de comercio, crearlo o aceptar una invitación
    devuelven el usuario visto desde ESE comercio."""
    _requested_team.set(team_id)


def _header_team_id(request):
    raw = (request.META.get("HTTP_X_TEAM_ID") or "").strip()
    return int(raw) if raw.isdigit() else None


class TeamCacheMiddleware:
    """Una caché de membresías por request y el comercio que pidió el
    frontend (``X-Team-Id``)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        with request_cache(_header_team_id(request)):
            return self.get_response(request)


def memberships(user) -> list[TeamMember]:
    """Las membresías de ``user`` en comercios abiertos, la más usada
    primero (``last_used_at``, después la más nueva)."""
    if not getattr(user, "pk", None):
        return []
    cache = _request_members.get()
    if cache is not None and user.pk in cache:
        return cache[user.pk]
    members = list(
        TeamMember.objects.select_related("team__owner", "team__data_user", "profile")
        .filter(user=user, team__closed_at=None)
        .order_by(F("last_used_at").desc(nulls_last=True), "-joined_at", "-id")
    )
    if cache is not None:
        cache[user.pk] = members
    return members


def membership(user) -> TeamMember | None:
    """La membresía con la que ``user`` trabaja ahora: la del comercio que
    pidió la request si es suyo; si no, la del último que usó. ``None`` si
    no está en ningún comercio (nunca crea uno)."""
    members = memberships(user)
    wanted = _requested_team.get()
    if wanted is not None:
        for member in members:
            if member.team_id == wanted:
                return member
    return members[0] if members else None


def mark_used(member):
    """Este pasa a ser el comercio en el que ``member.user`` entra sin elegir."""
    member.last_used_at = timezone.now()
    member.save(update_fields=["last_used_at"])
    forget(member.user)


def team_for(user) -> Team:
    member = membership(user)
    if member is None:
        raise NoTeam()
    return member.team


def data_owner(user):
    """El usuario del que cuelgan los datos del comercio actual de ``user``."""
    return team_for(user).data_user


def accesses_of(member) -> set[str]:
    """Los accesos de una membresía: el dueño, todos; un empleado, los de su
    perfil o (personalizado) los suyos."""
    if member is None:
        return set()
    if member.role == TeamMember.Role.OWNER:
        return set(ACCESSES)
    chosen = member.profile.accesses if member.profile_id else member.accesses
    return {access for access in chosen if access in ACCESSES}


def member_accesses(user) -> set[str]:
    """Los accesos del usuario en su comercio actual."""
    return accesses_of(membership(user))


def can_access(user, access: str) -> bool:
    return access in member_accesses(user)


# --- El usuario de datos no es una persona -----------------------------------


def team_of_data_user(user) -> Team | None:
    """El comercio del que ``user`` es el usuario de datos, o None."""
    if user is None or not getattr(user, "pk", None):
        return None
    try:
        return user.data_team
    except Team.DoesNotExist:
        return None


def mail_person(user):
    """A quién se le escribe por los datos de ``user``: para un usuario de
    datos, la persona dueña (si el comercio sigue abierto y ella está
    activa); para una persona, ella misma si está activa."""
    team = team_of_data_user(user)
    if team is not None:
        if team.is_closed or team.owner is None or not team.owner.is_active:
            return None
        return team.owner
    return user if user is not None and user.is_active else None


def business_display_name(user) -> str:
    """Cómo se nombra a quien es dueño de unos datos: el comercio para un
    usuario de datos; nombre o mail para una persona."""
    team = team_of_data_user(user)
    if team is not None:
        return team_display_name(team)
    return (user.get_full_name() or user.email) if user is not None else ""


def contact_email(user) -> str:
    """El mail para mostrar del dueño de unos datos (el de la persona dueña
    si es un usuario de datos)."""
    team = team_of_data_user(user)
    if team is not None:
        return team.owner.email if team.owner else ""
    return user.email if user is not None else ""


def team_display_name(team) -> str:
    return team.name or f"Comercio de {team.owner.email if team.owner else '(sin dueño)'}"


def internal_users_q(prefix="") -> Q:
    """Los usuarios de datos de los comercios (no son personas: no se listan
    ni se cuentan ni se tocan desde la administración). El de un comercio cuyo
    usuario de datos es su propio dueño sí es una persona."""
    return Q(**{f"{prefix}data_team__isnull": False}) & ~Q(**{f"{prefix}data_team__owner": F(f"{prefix}pk")})


def live_data_owner_q(prefix="") -> Q:
    """Un dueño de datos que puede seguir trabajando: el usuario de datos de
    un comercio abierto cuya persona dueña está activa (si el admin la
    desactiva, sus links y agentes dejan de servir, como antes), o una
    persona activa sin comercio propio de datos."""
    return Q(
        **{
            f"{prefix}data_team__isnull": False,
            f"{prefix}data_team__closed_at__isnull": True,
            f"{prefix}data_team__owner__is_active": True,
        }
    ) | Q(**{f"{prefix}data_team__isnull": True, f"{prefix}is_active": True})


def audit_team_id(actor):
    """El comercio en el que pasó lo que se audita: el del usuario de datos,
    o el comercio actual de la persona."""
    if actor is None or not getattr(actor, "pk", None):
        return None
    team = team_of_data_user(actor)
    if team is not None:
        return team.pk
    member = membership(actor)
    return member.team_id if member is not None else None


# --- Tiendas por empleado ---------------------------------------------------
# Un empleado puede estar limitado a algunas tiendas del comercio: ve sus
# pedidos y los cargados a mano (que no son de ninguna tienda), y nada más.
# Plantillas, transportistas e impresoras son del comercio entero.


def allowed_store_ids(user) -> set[int] | None:
    """Ids de las tiendas que ve ``user``; ``None`` = todas (dueño, o
    empleado con "todas las tiendas")."""
    return stores_of(membership(user))


def stores_of(member) -> set[int] | None:
    """Las tiendas de una membresía; ``None`` = todas. Sin membresía, ninguna."""
    if member is None:
        return set()
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
