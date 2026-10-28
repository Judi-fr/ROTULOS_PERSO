"""Lo que cambia la composición de un comercio: crearlo, invitar, aceptar,
pedir sumarse con el código, aprobar, quitar, salir, cambiar accesos, pasar
la titularidad, la asignación del admin y la baja.

Reglas del diseño (``docs/superpowers/specs/2026-10-19-equipos-design.md``):
una persona puede estar en varios comercios; nadie entra sin aceptar o sin
que lo aprueben (salvo que lo ponga el admin); quien reparte accesos no
puede dar lo que no tiene, ni tocarse a sí mismo ni al dueño; y nunca se
borran datos de nadie.
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
from .team_models import Team, TeamInvitation, TeamJoinRequest, TeamMember, TeamProfile
from .teams import (
    ACCESSES,
    accesses_of,
    create_team,
    forget,
    new_join_code,
    normalize_join_code,
    stores_of,
)

logger = logging.getLogger(__name__)

# Los datos del comercio: (modelo, campo que apunta a su usuario de datos,
# cómo se nombran en un mensaje). Lo usan la lista de comercios dados de baja
# y la migración que creó los usuarios de datos.
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
    if team.is_closed:
        raise TeamError("Ese comercio está dado de baja.")
    if team.members.filter(user=user).exists():
        raise TeamError("Ya es parte de este comercio.")


def _join(user, team, *, role, profile=None, accesses=(), all_stores=True, store_ids=()):
    """Suma a ``user`` al comercio ``team`` (además de los que ya tiene), que
    pasa a ser el último que usó."""
    _check_can_join(user, team)
    with transaction.atomic():
        member = TeamMember.objects.create(
            team=team,
            user=user,
            role=role,
            profile=profile,
            accesses=list(accesses) if profile is None else [],
            all_stores=all_stores,
            last_used_at=timezone.now(),
        )
        if not all_stores:
            member.stores.set(store_ids)
    forget(user)
    return member


def create_business(user, name):
    """La persona crea un comercio propio (uno más: no hay límite)."""
    name = (name or "").strip()[:150]
    if not name:
        raise TeamError("Poné el nombre del comercio.")
    return create_team(user, name=name)


# --- Validación de lo que se reparte ----------------------------------------


def clean_accesses(accesses):
    unknown = [access for access in accesses or [] if access not in ACCESSES]
    if unknown:
        raise TeamError(f"Accesos desconocidos: {', '.join(unknown)}.")
    return sorted(set(accesses or []))


def _member_in(user, team):
    return TeamMember.objects.select_related("profile").filter(team=team, user=user).first()


def _accesses_in(user, team):
    """Los accesos de ``user`` en ESE comercio (no en su comercio actual)."""
    return accesses_of(_member_in(user, team))


def _stores_in(user, team):
    return stores_of(_member_in(user, team))


def _check_grant(by_user, team, *, profile, accesses, all_stores, store_ids):
    """Quien reparte no puede dar más de lo que tiene (accesos y tiendas)."""
    if profile is not None and profile.team_id != team.id:
        raise TeamError("Ese perfil no es de este comercio.")
    granted = set(profile.accesses if profile is not None else accesses)
    missing = granted - _accesses_in(by_user, team)
    if missing:
        raise TeamForbidden("No podés dar accesos que vos no tenés: " + ", ".join(ACCESSES[a] for a in sorted(missing)) + ".")
    team_stores = set(
        apps.get_model("integrations.StoreConnection").objects.filter(owner=team.data_user).values_list("id", flat=True)
    )
    if not all_stores and not set(store_ids) <= team_stores:
        raise TeamError("Alguna de las tiendas elegidas no es de este comercio.")
    mine = _stores_in(by_user, team)
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
    ).select_related("team__owner", "invited_by", "profile").filter(team__closed_at=None)


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
    mine_member = _member_in(by_user, member.team)
    if mine_member is not None and mine_member.role == TeamMember.Role.OWNER:
        return
    # Quien no es dueño solo toca a quien no tiene más que él: ni accesos ni
    # tiendas que él no tenga (si no, un "Equipo + Ver pedidos" podría sacar
    # a alguien con "Todo").
    if not accesses_of(member) <= accesses_of(mine_member):
        raise TeamForbidden("No podés cambiar ni quitar a alguien con accesos que vos no tenés.")
    mine, theirs = stores_of(mine_member), stores_of(member)
    if mine is not None and (theirs is None or not theirs <= mine):
        raise TeamForbidden("No podés cambiar ni quitar a alguien con tiendas que vos no tenés.")


def check_can_edit_profile(profile, by_user):
    """Cambiar los accesos de un perfil cambia a todos los que lo usan: quien
    no es dueño no puede tocar así a alguien que no podría tocar de a uno
    (un perfil con accesos que él no tiene, o que usa alguien con tiendas
    que él no tiene)."""
    mine_member = _member_in(by_user, profile.team)
    if mine_member is not None and mine_member.role == TeamMember.Role.OWNER:
        return
    if not set(profile.accesses) <= accesses_of(mine_member):
        raise TeamForbidden("No podés cambiar un perfil con accesos que vos no tenés.")
    mine = stores_of(mine_member)
    if mine is None:
        return
    for member in profile.members.all():
        theirs = stores_of(member)
        if theirs is None or not theirs <= mine:
            raise TeamForbidden("No podés cambiar un perfil que usa alguien con tiendas que vos no tenés.")


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
    """Lo saca del comercio; lo que hizo queda en el comercio y él sigue con
    los otros comercios que tenga (o con ninguno)."""
    _check_can_touch(member, by_user)
    user = member.user
    member.delete()
    forget(user)


def leave(member):
    """La persona se va sola del comercio. El dueño no: primero pasa la
    titularidad o da de baja el comercio."""
    if member.role == TeamMember.Role.OWNER:
        raise TeamError("Sos el dueño: para irte, pasá la titularidad a otra persona o da de baja el comercio.")
    user = member.user
    member.delete()
    forget(user)


def transfer_ownership(team, by_user, new_owner_member):
    """Solo el dueño. Los datos no se mueven (son del usuario de datos del
    comercio): cambian los roles, y el viejo queda como empleado con el
    perfil "Todo"."""
    if team.owner_id != by_user.id:
        raise TeamForbidden("Solo el dueño puede pasar la titularidad.")
    if new_owner_member.team_id != team.id or new_owner_member.role == TeamMember.Role.OWNER:
        raise TeamError("Elegí a un empleado del comercio.")
    old_owner, new_owner = team.owner, new_owner_member.user
    with transaction.atomic():
        # Primero el viejo deja de ser dueño: hay un solo dueño por comercio.
        everything, _ = TeamProfile.objects.get_or_create(team=team, name="Todo", defaults={"accesses": list(ACCESSES)})
        TeamMember.objects.filter(team=team, user=old_owner).update(
            role=TeamMember.Role.EMPLOYEE, profile=everything, accesses=[], all_stores=True
        )
        team.owner = new_owner
        team.save(update_fields=["owner"])
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
    """El admin suma a ``user`` a ``team`` sin invitación (sin sacarlo de sus
    otros comercios); como dueño, además pasa la titularidad."""
    if role not in (TeamMember.Role.OWNER, TeamMember.Role.EMPLOYEE):
        raise TeamError("El rol tiene que ser dueño o empleado.")
    current = _member_in(user, team)
    if current is None:
        everything = team.profiles.filter(name="Todo").first()
        current = _join(user, team, role=TeamMember.Role.EMPLOYEE, profile=everything)
    if role == TeamMember.Role.OWNER and current.role != TeamMember.Role.OWNER:
        transfer_ownership(team, team.owner, current)
        current.refresh_from_db()
    return current


# --- Código del comercio y pedidos para sumarse --------------------------------


def request_join(user, code):
    """Pide sumarse con el código: queda pendiente hasta que lo aprueben.
    Un código que no existe y uno apagado dan el mismo mensaje (no se puede
    saber qué códigos existen probando)."""
    code = normalize_join_code(code)
    team = Team.objects.filter(join_code=code, join_code_enabled=True, closed_at=None).first() if code else None
    if team is None:
        raise TeamError("Ese código no sirve: revisalo o pedile uno nuevo a quien te lo pasó.")
    if team.members.filter(user=user).exists():
        raise TeamError("Ya sos parte de ese comercio.")
    pending = team.join_requests.filter(user=user, status=TeamJoinRequest.Status.PENDING).first()
    if pending is not None:
        return pending, False
    return TeamJoinRequest.objects.create(team=team, user=user), True


def pending_join_requests(team):
    return team.join_requests.filter(status=TeamJoinRequest.Status.PENDING).select_related(
        "user", "user__personal_profile"
    )


def _decide(join_request, by_user, status):
    join_request.status = status
    join_request.decided_by = by_user
    join_request.decided_at = timezone.now()
    join_request.save(update_fields=["status", "decided_by", "decided_at"])


def approve_join(join_request, by_user, *, profile=None, accesses=(), all_stores=True, store_ids=()):
    """Lo suma con el perfil y las tiendas que eligió quien aprueba (mismas
    reglas que una invitación: no se da lo que no se tiene)."""
    team = join_request.team
    if join_request.status != TeamJoinRequest.Status.PENDING:
        raise TeamError("Ese pedido ya no está pendiente.")
    accesses = clean_accesses(accesses)
    _check_grant(by_user, team, profile=profile, accesses=accesses, all_stores=all_stores, store_ids=store_ids)
    with transaction.atomic():
        member = _join(
            join_request.user,
            team,
            role=TeamMember.Role.EMPLOYEE,
            profile=profile,
            accesses=accesses,
            all_stores=all_stores,
            store_ids=store_ids,
        )
        # Lo aprobaron, pero no lo usó: no le cambia el comercio en el que
        # entra la próxima vez.
        member.last_used_at = None
        member.save(update_fields=["last_used_at"])
        _decide(join_request, by_user, TeamJoinRequest.Status.APPROVED)
    forget(join_request.user)
    return member


def reject_join(join_request, by_user):
    if join_request.status != TeamJoinRequest.Status.PENDING:
        raise TeamError("Ese pedido ya no está pendiente.")
    _decide(join_request, by_user, TeamJoinRequest.Status.REJECTED)


def cancel_join(join_request):
    _decide(join_request, join_request.user, TeamJoinRequest.Status.CANCELLED)


def set_join_code_enabled(team, enabled):
    team.join_code_enabled = bool(enabled)
    team.save(update_fields=["join_code_enabled"])


def regenerate_join_code(team):
    """Código nuevo (el viejo deja de servir) y prendido."""
    team.join_code = new_join_code()
    team.join_code_enabled = True
    team.save(update_fields=["join_code", "join_code_enabled"])



# --- Baja del comercio --------------------------------------------------------

CLOSE_CONFIRM_WORD = "BAJA"

# Lo que una baja apaga para que el comercio deje de trabajar solo (sin
# borrar nada) y la reapertura vuelve a prender: (clave en
# ``Team.closed_state``, modelo, campo del dueño).
_SWITCHED_OFF = [
    ("carrier_accounts", "carriers.CarrierAccount", "owner"),
    ("print_agents", "printing.PrintAgent", "owner"),
    ("integration_keys", "integrations.IntegrationKey", "owner"),
    ("incoming_webhooks", "integrations.IncomingWebhook", "owner"),
    ("webhook_endpoints", "integrations.WebhookEndpoint", "owner"),
]


def _archive_store_id(external_store_id, team):
    """El id de la tienda con una marca de la baja: libera ``(platform,
    external_store_id)`` para que esa tienda se pueda volver a conectar en
    otro comercio sin mezclarse con los pedidos archivados."""
    suffix = f"#baja-{team.pk}"
    return external_store_id[: 255 - len(suffix)] + suffix


def close_team(team, by_user, *, confirm, reason=""):
    """Da de baja el comercio sin borrar nada (soft delete).

    Los datos quedan donde estaban, en el usuario de datos del comercio;
    nadie sigue siendo miembro, así que nadie los ve. Cada persona sigue con
    su cuenta y con sus otros comercios. Las tiendas se desconectan (y su id
    se marca, para poder volver a conectarlas), y se apagan transportistas,
    agentes de impresión, claves de API, webhooks y el código para sumarse;
    las invitaciones y los pedidos para sumarse pendientes se cancelan.
    """
    from apps.integrations.models import StoreConnection
    from apps.printing.models import PrintJob

    if team.is_closed:
        raise TeamError("Este comercio ya está dado de baja.")
    if team.owner_id != by_user.id:
        raise TeamForbidden("Solo el dueño puede dar de baja el comercio.")
    if (confirm or "").strip().upper() != CLOSE_CONFIRM_WORD:
        raise TeamError(f"Para confirmar escribí {CLOSE_CONFIRM_WORD}.")

    data_user = team.data_user
    now = timezone.now()
    with transaction.atomic():
        state = {"stores": {}}
        for store in StoreConnection.objects.select_for_update().filter(owner=data_user):
            state["stores"][str(store.pk)] = store.external_store_id
            store.status = StoreConnection.Status.REVOKED
            store.clear_tokens()
            store.disconnected_at = now
            store.external_store_id = _archive_store_id(store.external_store_id, team)
            store.save()
        for key, label, field in _SWITCHED_OFF:
            rows = apps.get_model(label).objects.filter(**{field: data_user, "is_active": True})
            state[key] = list(rows.values_list("pk", flat=True))
            rows.update(is_active=False)
        PrintJob.objects.filter(owner=data_user, status=PrintJob.Status.PENDING).update(status=PrintJob.Status.CANCELLED)
        apps.get_model("monitoring.MerchantNotice").objects.filter(owner=data_user, resolved_at=None).update(resolved_at=now)
        team.invitations.filter(status=TeamInvitation.Status.PENDING).update(status=TeamInvitation.Status.CANCELLED)
        team.join_requests.filter(status=TeamJoinRequest.Status.PENDING).update(
            status=TeamJoinRequest.Status.CANCELLED, decided_at=now
        )

        people = [member.user for member in team.members.select_related("user")]
        team.members.all().delete()
        team.closed_at = now
        team.closed_by = by_user
        team.close_reason = (reason or "").strip()[:2000]
        team.closed_state = state
        team.join_code_enabled = False
        team.save()
    for person in people:
        forget(person)
    return team


def reopen_team(team):
    """El admin le devuelve el comercio a su dueño, como un comercio más de
    los que tenga. Los empleados no vuelven solos: el dueño los invita. Las
    tiendas quedan desconectadas (sus tokens se descartaron): el dueño las
    vuelve a conectar. Devuelve los avisos de lo que no se pudo restaurar."""
    from apps.integrations.models import StoreConnection

    if not team.is_closed:
        raise TeamError("Este comercio no está dado de baja.")
    owner = team.owner
    if owner is None:
        raise TeamError("El dueño de este comercio ya no existe: no hay a quién devolvérselo.")

    data_user = team.data_user
    state = team.closed_state or {}
    warnings = []
    with transaction.atomic():
        for pk, external_store_id in (state.get("stores") or {}).items():
            store = StoreConnection.objects.filter(pk=pk, owner=data_user).first()
            if store is None:
                continue
            taken = StoreConnection.objects.filter(
                platform=store.platform, external_store_id=external_store_id
            ).exclude(pk=store.pk)
            if taken.exists():
                warnings.append(
                    f"La tienda «{store.name or external_store_id}» ya está conectada en otro comercio: "
                    "quedó archivada."
                )
                continue
            store.external_store_id = external_store_id
            store.save(update_fields=["external_store_id", "updated_at"])
        for key, label, field in _SWITCHED_OFF:
            ids = state.get(key) or []
            if ids:
                apps.get_model(label).objects.filter(pk__in=ids, **{field: data_user}).update(is_active=True)
        team.members.all().delete()
        team.closed_at = None
        team.closed_by = None
        team.close_reason = ""
        team.closed_state = {}
        team.save()
        TeamMember.objects.create(team=team, user=owner, role=TeamMember.Role.OWNER)
    forget(owner)
    return warnings
