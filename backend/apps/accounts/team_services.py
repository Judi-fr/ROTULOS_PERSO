"""Lo que cambia la composición de un comercio: invitar, aceptar, quitar,
cambiar accesos, pasar la titularidad y la asignación del admin.

Reglas del diseño (``docs/superpowers/specs/2026-10-19-equipos-design.md``):
un usuario pertenece a UN comercio; nadie entra sin aceptar (salvo que lo
ponga el admin); quien reparte accesos no puede dar lo que no tiene, ni
tocarse a sí mismo ni al dueño; y nunca se borran datos de nadie.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from django.apps import apps
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import user_email_verified
from .team_models import TeamInvitation, TeamMember, TeamProfile
from .teams import ACCESSES, allowed_store_ids, create_team, forget, member_accesses, membership

logger = logging.getLogger(__name__)

# Los datos del comercio: (modelo, campo que apunta al dueño, cómo se
# nombran en un mensaje). Lo usan "comercio vacío" y pasar la titularidad.
OWNED_DATA = [
    ("integrations.StoreConnection", "owner", "tienda(s)"),
    ("orders.Order", "user", "pedido(s)"),
    ("orders.Address", "user", "dirección(es)"),
    ("orders.OrderImport", "user", "importación(es)"),
    ("orders.ImportMapping", "user", "mapeo(s) de importación"),
    ("labels.LabelTemplate", "owner", "plantilla(s)"),
    ("labels.Label", "user", "rótulo(s)"),
    ("labels.LabelSequence", "owner", "numeración(es)"),
    ("documents.Document", "user", "documento(s)"),
    ("carriers.CarrierAccount", "owner", "cuenta(s) de transportista"),
    ("printing.PrintAgent", "owner", "agente(s) de impresión"),
    ("printing.Printer", "owner", "impresora(s)"),
    ("printing.PrintJob", "owner", "trabajo(s) de impresión"),
    ("integrations.IntegrationKey", "owner", "clave(s) de API"),
    ("integrations.IncomingWebhook", "owner", "webhook(s) entrante(s)"),
    ("integrations.WebhookEndpoint", "owner", "webhook(s) saliente(s)"),
    ("accounts.AccountSettings", "user", "datos del comercio"),
]


class TeamError(Exception):
    """Algo que no se puede hacer, con el motivo para el usuario."""


class TeamForbidden(TeamError):
    """No tiene permiso para hacerlo (403)."""


class TeamMailError(TeamError):
    """El mail de la invitación no salió (502): no queda nada a medias."""


def owned_data_count(user) -> dict[str, int]:
    """Cuántas filas de datos cuelgan de ``user``; ``{}`` = comercio vacío."""
    counts = {}
    for label, field, name in OWNED_DATA:
        count = apps.get_model(label).objects.filter(**{field: user}).count()
        if count:
            counts[name] = count
    return counts


def _describe(counts):
    return ", ".join(f"{count} {name}" for name, count in counts.items())


def _check_can_join(user, team):
    """Un usuario solo se suma a otro comercio si el suyo está vacío."""
    current = membership(user)
    if current.team_id == team.id:
        raise TeamError("Ya es parte de este comercio.")
    if current.role == TeamMember.Role.OWNER and current.team.members.exclude(user=user).exists():
        raise TeamError("Tiene su propio comercio con empleados: no se puede sumar a otro.")
    counts = owned_data_count(user)
    if counts:
        raise TeamError(f"Ya tiene un comercio propio con {_describe(counts)}; no se puede sumar a otro.")
    return current


def _join(user, team, *, role, profile=None, accesses=(), all_stores=True, store_ids=()):
    """Pasa a ``user`` al comercio ``team``: descarta su comercio vacío."""
    current = _check_can_join(user, team)
    with transaction.atomic():
        if current.role == TeamMember.Role.OWNER:
            current.team.delete()  # vacío: solo sus perfiles sugeridos
        else:
            current.delete()
        member = TeamMember.objects.create(
            team=team,
            user=user,
            role=role,
            profile=profile,
            accesses=list(accesses) if profile is None else [],
            all_stores=all_stores,
        )
        if not all_stores:
            member.stores.set(store_ids)
    forget(user)
    return member


# --- Validación de lo que se reparte ----------------------------------------


def clean_accesses(accesses):
    unknown = [access for access in accesses or [] if access not in ACCESSES]
    if unknown:
        raise TeamError(f"Accesos desconocidos: {', '.join(unknown)}.")
    return sorted(set(accesses or []))


def _check_grant(by_user, team, *, profile, accesses, all_stores, store_ids):
    """Quien reparte no puede dar más de lo que tiene (accesos y tiendas)."""
    if profile is not None and profile.team_id != team.id:
        raise TeamError("Ese perfil no es de este comercio.")
    granted = set(profile.accesses if profile is not None else accesses)
    missing = granted - member_accesses(by_user)
    if missing:
        raise TeamForbidden("No podés dar accesos que vos no tenés: " + ", ".join(ACCESSES[a] for a in sorted(missing)) + ".")
    team_stores = set(
        apps.get_model("integrations.StoreConnection").objects.filter(owner=team.owner).values_list("id", flat=True)
    )
    if not all_stores and not set(store_ids) <= team_stores:
        raise TeamError("Alguna de las tiendas elegidas no es de este comercio.")
    mine = allowed_store_ids(by_user)
    if mine is not None and (all_stores or not set(store_ids) <= mine):
        raise TeamForbidden("No podés dar tiendas que vos no tenés.")


# --- Invitaciones -------------------------------------------------------------


def _send_invitation(invitation):
    """Manda el mail; si falla, ``TeamMailError`` (el llamador deshace)."""
    try:
        _mail_invitation(invitation)
    except Exception as exc:  # noqa: BLE001 - cualquier error del SMTP
        logger.exception("No se pudo mandar la invitación a %s", invitation.email)
        raise TeamMailError(f"No se pudo mandar el mail de la invitación: {exc}") from exc


def _mail_invitation(invitation):
    team_name = str(invitation.team)
    who = invitation.invited_by.email if invitation.invited_by else "El dueño"
    send_mail(
        f"Te invitaron al comercio {team_name}",
        (
            f"{who} te invitó a trabajar en el comercio «{team_name}» en Rótulos.\n\n"
            "Para aceptar, entrá con tu cuenta (o creala con este mismo mail) y abrí:\n"
            f"{settings.TEAM_INVITATION_URL}\n\n"
            f"La invitación vence el {timezone.localtime(invitation.expires_at):%d/%m/%Y}.\n"
            "Si no esperabas este mail, ignoralo: nadie entra a un comercio sin aceptar."
        ),
        settings.DEFAULT_FROM_EMAIL,
        [invitation.email],
        fail_silently=False,
    )


def invite(team, by_user, email, *, profile=None, accesses=(), all_stores=True, store_ids=()):
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise TeamError("Poné un mail válido.")
    accesses = clean_accesses(accesses)
    _check_grant(by_user, team, profile=profile, accesses=accesses, all_stores=all_stores, store_ids=store_ids)
    if team.members.filter(user__email__iexact=email).exists():
        raise TeamError("Esa persona ya es parte del comercio.")
    if team.invitations.filter(email__iexact=email, status=TeamInvitation.Status.PENDING, expires_at__gt=timezone.now()).exists():
        raise TeamError("Ya hay una invitación pendiente para ese mail: podés reenviarla.")
    # El mail va DENTRO de la transacción: si no sale, la invitación no
    # queda creada (si quedara, el siguiente intento diría "ya hay una
    # invitación pendiente" para una que nunca llegó).
    with transaction.atomic():
        invitation = TeamInvitation.objects.create(
            team=team,
            email=email,
            profile=profile,
            accesses=accesses if profile is None else [],
            all_stores=all_stores,
            invited_by=by_user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timedelta(days=settings.TEAM_INVITATION_DAYS),
        )
        if not all_stores:
            invitation.stores.set(store_ids)
        _send_invitation(invitation)
    return invitation


def pending_invitations_for(user):
    return TeamInvitation.objects.filter(
        email__iexact=user.email, status=TeamInvitation.Status.PENDING, expires_at__gt=timezone.now()
    ).select_related("team__owner", "invited_by", "profile")


def accept(invitation, user):
    if not user_email_verified(user):
        raise TeamForbidden("Primero verificá tu mail: la invitación es para quien lo controla.")
    with transaction.atomic():
        member = _join(
            user,
            invitation.team,
            role=TeamMember.Role.EMPLOYEE,
            profile=invitation.profile,
            accesses=invitation.accesses,
            all_stores=invitation.all_stores,
            store_ids=list(invitation.stores.values_list("id", flat=True)),
        )
        invitation.status = TeamInvitation.Status.ACCEPTED
        invitation.save(update_fields=["status"])
    return member


def decline(invitation):
    invitation.status = TeamInvitation.Status.DECLINED
    invitation.save(update_fields=["status"])


def cancel(invitation):
    invitation.status = TeamInvitation.Status.CANCELLED
    invitation.save(update_fields=["status"])


def resend(invitation):
    with transaction.atomic():
        invitation.expires_at = timezone.now() + timedelta(days=settings.TEAM_INVITATION_DAYS)
        invitation.save(update_fields=["expires_at"])
        _send_invitation(invitation)


# --- Miembros -----------------------------------------------------------------


def _check_can_touch(member, by_user):
    if member.role == TeamMember.Role.OWNER:
        raise TeamForbidden("Al dueño no se lo puede cambiar ni quitar.")
    if member.user_id == by_user.id:
        raise TeamForbidden("No podés cambiar tus propios accesos.")
    if membership(by_user).role == TeamMember.Role.OWNER:
        return
    # Quien no es dueño solo toca a quien no tiene más que él: ni accesos ni
    # tiendas que él no tenga (si no, un "Equipo + Ver pedidos" podría sacar
    # a alguien con "Todo").
    if not member_accesses(member.user) <= member_accesses(by_user):
        raise TeamForbidden("No podés cambiar ni quitar a alguien con accesos que vos no tenés.")
    mine, theirs = allowed_store_ids(by_user), allowed_store_ids(member.user)
    if mine is not None and (theirs is None or not theirs <= mine):
        raise TeamForbidden("No podés cambiar ni quitar a alguien con tiendas que vos no tenés.")


def update_member(member, by_user, *, profile, accesses, all_stores, store_ids):
    _check_can_touch(member, by_user)
    accesses = clean_accesses(accesses)
    _check_grant(by_user, member.team, profile=profile, accesses=accesses, all_stores=all_stores, store_ids=store_ids)
    with transaction.atomic():
        member.profile = profile
        member.accesses = accesses if profile is None else []
        member.all_stores = all_stores
        member.save(update_fields=["profile", "accesses", "all_stores"])
        member.stores.set([] if all_stores else store_ids)
    forget(member.user)
    return member


def remove_member(member, by_user):
    """Lo saca del comercio; lo que creó queda en el comercio y él arranca
    con un comercio propio vacío."""
    _check_can_touch(member, by_user)
    user = member.user
    with transaction.atomic():
        member.delete()
        create_team(user)
    forget(user)


def transfer_ownership(team, by_user, new_owner_member):
    """Solo el dueño. Las filas de datos pasan del dueño viejo al nuevo, y el
    viejo queda como empleado con el perfil "Todo"."""
    if team.owner_id != by_user.id:
        raise TeamForbidden("Solo el dueño puede pasar la titularidad.")
    if new_owner_member.team_id != team.id or new_owner_member.role == TeamMember.Role.OWNER:
        raise TeamError("Elegí a un empleado del comercio.")
    old_owner, new_owner = team.owner, new_owner_member.user
    with transaction.atomic():
        for label, field, _ in OWNED_DATA:
            apps.get_model(label).objects.filter(**{field: old_owner}).update(**{field: new_owner})
        team.owner = new_owner
        team.save(update_fields=["owner"])
        everything, _ = TeamProfile.objects.get_or_create(team=team, name="Todo", defaults={"accesses": list(ACCESSES)})
        TeamMember.objects.filter(team=team, user=old_owner).update(
            role=TeamMember.Role.EMPLOYEE, profile=everything, accesses=[], all_stores=True
        )
        new_owner_member.role = TeamMember.Role.OWNER
        new_owner_member.profile = None
        new_owner_member.accesses = []
        new_owner_member.all_stores = True
        new_owner_member.save(update_fields=["role", "profile", "accesses", "all_stores"])
        new_owner_member.stores.clear()
    forget(old_owner)
    forget(new_owner)
    return team


def admin_assign(user, team, role):
    """El admin pone a ``user`` en ``team`` sin invitación. Misma regla del
    comercio vacío; como dueño, además pasa la titularidad."""
    if role not in (TeamMember.Role.OWNER, TeamMember.Role.EMPLOYEE):
        raise TeamError("El rol tiene que ser dueño o empleado.")
    current = membership(user)
    if current.team_id != team.id:
        everything = team.profiles.filter(name="Todo").first()
        current = _join(user, team, role=TeamMember.Role.EMPLOYEE, profile=everything)
    if role == TeamMember.Role.OWNER and current.role != TeamMember.Role.OWNER:
        transfer_ownership(team, team.owner, current)
    return membership(user)

