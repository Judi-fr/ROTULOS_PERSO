"""``/api/v1/team/``: el equipo del comercio actual (miembros, perfiles,
invitaciones, código para sumarse, titularidad), lo de la propia persona
(crear un comercio, cambiar de comercio, sumarse, salir, sus invitaciones y
pedidos). Más la asignación de comercio del admin
(``/api/v1/users/<id>/team/``).

La lógica vive en ``team_services.py``; acá solo se lee el pedido, se llama
y se audita. Identidad siempre desde ``request.user``.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.generics import ListAPIView
from rest_framework.views import APIView

from apps.audit.serializers import AuditLogSerializer
from apps.audit.services import record
from apps.audit.views import AuditLogPagination, filter_logs
from apps.integrations.models import StoreConnection

from . import team_services as services
from .auth_views import user_payload
from .personal_profiles import public_fields
from .permissions_map import TEAM_SCOPED_PERMISSIONS
from .role_permissions import HasRolePermission
from .team_models import Team, TeamInvitation, TeamJoinRequest, TeamMember, TeamProfile
from .teams import (
    ACCESSES,
    NoTeam,
    accesses_of,
    mark_used,
    member_accesses,
    membership,
    memberships,
    store_scope_q,
    team_display_name,
    use_team,
)

User = get_user_model()
TEAM_PERMISSION = "team.manage"


def _error(exc):
    if isinstance(exc, services.TeamMailError):
        code = status.HTTP_502_BAD_GATEWAY
    elif isinstance(exc, services.TeamForbidden):
        code = status.HTTP_403_FORBIDDEN
    else:
        code = status.HTTP_400_BAD_REQUEST
    return Response({"detail": str(exc)}, status=code)


def _audit(request, action, target, target_repr, changes=None, actor=None):
    record(
        request,
        actor=actor,
        category="team",
        action=action,
        target=target,
        target_type=target._meta.model_name,
        target_repr=target_repr,
        changes=changes,
    )


def _store_choice(data):
    """``(all_stores, store_ids)`` del cuerpo."""
    all_stores = data.get("all_stores", True) is not False
    raw = data.get("store_ids") or []
    if not isinstance(raw, list) or not all(str(item).isdigit() for item in raw):
        raise services.TeamError("store_ids tiene que ser una lista de ids.")
    return all_stores, [int(item) for item in raw]


def _profile(team, value):
    if value in (None, ""):
        return None
    return get_object_or_404(TeamProfile, pk=value, team=team)


def member_data(member, request=None):
    user = member.user
    return {
        "id": member.id,
        "user_id": user.id,
        "email": user.email,
        "name": " ".join(filter(None, [user.first_name, user.last_name])),
        # Su perfil personal: nombre para mostrar, puesto, teléfono y foto.
        **public_fields(user, request),
        "role": member.role,
        "profile": member.profile_id,
        "profile_name": member.profile.name if member.profile_id else "",
        "custom_accesses": member.accesses,
        "accesses": sorted(accesses_of(member)),
        "all_stores": member.role == TeamMember.Role.OWNER or member.all_stores,
        "store_ids": sorted(member.stores.values_list("id", flat=True)),
        "joined_at": member.joined_at,
    }


def invitation_data(invitation):
    return {
        "id": invitation.id,
        "email": invitation.email,
        "profile": invitation.profile_id,
        "profile_name": invitation.profile.name if invitation.profile_id else "",
        "accesses": invitation.accesses,
        "all_stores": invitation.all_stores,
        "store_ids": sorted(invitation.stores.values_list("id", flat=True)),
        "invited_by": invitation.invited_by.email if invitation.invited_by else "",
        "expires_at": invitation.expires_at,
        "created_at": invitation.created_at,
    }


def team_name(team):
    return team_display_name(team)


def join_request_data(join_request, request=None):
    user = join_request.user
    return {
        "id": join_request.id,
        "user_id": user.id,
        "email": user.email,
        "name": " ".join(filter(None, [user.first_name, user.last_name])),
        **public_fields(user, request),
        "created_at": join_request.created_at,
    }


def _user_response(request, team_id=None, **extra):
    """El usuario visto desde ``team_id`` (o desde su comercio actual)."""
    if team_id is not None:
        use_team(team_id)
    return Response({**extra, "user": user_payload(request.user, request=request)})


def _current_member(request):
    member = membership(request.user)
    if member is None:
        raise NoTeam()
    return member


class _TeamView(APIView):
    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(TEAM_PERMISSION)]

    def team(self):
        return _current_member(self.request).team


class TeamView(_TeamView):
    """GET / — todo lo del equipo; PATCH / ``{name}``."""

    def get(self, request):
        team = self.team()
        members = team.members.select_related("user", "user__personal_profile", "profile")
        members = sorted(members, key=lambda m: (m.role != TeamMember.Role.OWNER, m.joined_at))
        invitations = team.invitations.filter(
            status=TeamInvitation.Status.PENDING, expires_at__gt=timezone.now()
        ).select_related("profile", "invited_by")
        profiles = team.profiles.annotate(member_count=Count("members"))
        stores = StoreConnection.objects.filter(store_scope_q(request.user), owner=team.data_user).order_by("name", "id")
        return Response(
            {
                "team": {
                    "id": team.id,
                    "name": team.name,
                    "display_name": team_name(team),
                    "owner_email": team.owner.email if team.owner else "",
                },
                "me": {"role": membership(request.user).role, "accesses": sorted(member_accesses(request.user))},
                "members": [member_data(member, request) for member in members],
                "invitations": [invitation_data(invitation) for invitation in invitations],
                "join_code": {"code": team.join_code, "enabled": team.join_code_enabled},
                "join_requests": [join_request_data(r, request) for r in services.pending_join_requests(team)],
                "profiles": [
                    {"id": p.id, "name": p.name, "accesses": p.accesses, "member_count": p.member_count} for p in profiles
                ],
                "accesses": ACCESSES,
                "stores": [{"id": s.id, "name": s.name or s.external_store_id, "platform": s.platform} for s in stores],
            }
        )

    def patch(self, request):
        team = self.team()
        name = str(request.data.get("name", "")).strip()[:150]
        before = team.name
        team.name = name
        team.save(update_fields=["name"])
        _audit(request, "team.update", team, team_name(team), {"name": {"from": before, "to": name}})
        return Response({"id": team.id, "name": team.name, "display_name": team_name(team)})


class MemberView(_TeamView):
    """PATCH/DELETE members/<id>/"""

    def get_member(self, pk):
        return get_object_or_404(TeamMember.objects.select_related("user", "profile"), pk=pk, team=self.team())

    def patch(self, request, pk):
        member = self.get_member(pk)
        data = request.data
        before = member_data(member)
        try:
            # Lo que no viene en el pedido queda como está (es un PATCH).
            if "all_stores" in data or "store_ids" in data:
                all_stores, store_ids = _store_choice(data)
            else:
                all_stores, store_ids = before["all_stores"], before["store_ids"]
            if "profile" in data:
                profile = _profile(member.team, data.get("profile"))
                accesses = data.get("accesses") or []
            elif "accesses" in data:
                profile, accesses = None, data.get("accesses") or []
            else:
                profile, accesses = member.profile, member.accesses
            services.update_member(
                member,
                request.user,
                profile=profile,
                accesses=accesses,
                all_stores=all_stores,
                store_ids=store_ids,
            )
        except services.TeamError as exc:
            return _error(exc)
        after = member_data(member, request)
        changes = {key: {"from": before[key], "to": after[key]} for key in ("profile", "accesses", "all_stores", "store_ids") if before[key] != after[key]}
        _audit(request, "team.member_update", member.user, member.user.email, changes)
        return Response(after)

    def delete(self, request, pk):
        member = self.get_member(pk)
        try:
            services.remove_member(member, request.user)
        except services.TeamError as exc:
            return _error(exc)
        _audit(request, "team.member_remove", member.user, member.user.email)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitationListView(_TeamView):
    """POST invitations/ ``{email, profile|accesses, all_stores, store_ids}``"""

    # Cada invitación manda un mail a cualquier dirección: con límite, para
    # que no se pueda usar para mandar mails en cantidad.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "team_invite"

    def post(self, request):
        team = self.team()
        try:
            all_stores, store_ids = _store_choice(request.data)
            invitation = services.invite(
                team,
                request.user,
                request.data.get("email"),
                profile=_profile(team, request.data.get("profile")),
                accesses=request.data.get("accesses") or [],
                all_stores=all_stores,
                store_ids=store_ids,
            )
        except services.TeamError as exc:
            return _error(exc)
        _audit(request, "team.invite", invitation, invitation.email)
        return Response(invitation_data(invitation), status=status.HTTP_201_CREATED)


class InvitationView(_TeamView):
    """DELETE invitations/<id>/ — cancelarla."""

    def get_invitation(self, pk):
        return get_object_or_404(TeamInvitation, pk=pk, team=self.team(), status=TeamInvitation.Status.PENDING)

    def delete(self, request, pk):
        invitation = self.get_invitation(pk)
        services.cancel(invitation)
        _audit(request, "team.invite_cancel", invitation, invitation.email)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvitationResendView(InvitationView):
    """POST invitations/<id>/resend/ — la renueva y manda el mail de nuevo."""

    http_method_names = ["post", "options"]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "team_invite"

    def post(self, request, pk):
        invitation = self.get_invitation(pk)
        try:
            services.resend(invitation)
        except services.TeamError as exc:
            return _error(exc)
        return Response(invitation_data(invitation))


class ProfileListView(_TeamView):
    """GET/POST profiles/"""

    def get(self, request):
        profiles = self.team().profiles.annotate(member_count=Count("members"))
        return Response([{"id": p.id, "name": p.name, "accesses": p.accesses, "member_count": p.member_count} for p in profiles])

    def post(self, request):
        return create_profile(request, self.team(), grantable=member_accesses(request.user))


# Crear / editar / borrar un perfil: lo mismo desde el equipo (``grantable`` =
# los accesos de quien reparte: nadie da lo que no tiene) y desde el admin de
# la plataforma (``grantable=None``: puede todo, AdminTeamProfileView).
def create_profile(request, team, grantable):
    name = str(request.data.get("name") or "").strip()[:60]
    if not name:
        return Response({"detail": "Poné un nombre."}, status=status.HTTP_400_BAD_REQUEST)
    if team.profiles.filter(name__iexact=name).exists():
        return Response({"detail": "Ya hay un perfil con ese nombre."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        accesses = services.clean_accesses(request.data.get("accesses"))
        if grantable is not None and set(accesses) - grantable:
            raise services.TeamForbidden("No podés armar un perfil con accesos que vos no tenés.")
    except services.TeamError as exc:
        return _error(exc)
    profile = TeamProfile.objects.create(team=team, name=name, accesses=accesses)
    _audit(request, "team.profile_update", profile, profile.name, {"accesses": {"from": None, "to": accesses}})
    return Response(
        {"id": profile.id, "name": profile.name, "accesses": profile.accesses, "member_count": 0},
        status=status.HTTP_201_CREATED,
    )


def update_profile(request, profile, grantable):
    team = profile.team
    before = {"name": profile.name, "accesses": profile.accesses}
    try:
        if "accesses" in request.data:
            accesses = services.clean_accesses(request.data.get("accesses"))
            if grantable is not None and set(accesses) - grantable:
                raise services.TeamForbidden("No podés dar accesos que vos no tenés.")
            profile.accesses = accesses
    except services.TeamError as exc:
        return _error(exc)
    if "name" in request.data:
        name = str(request.data.get("name") or "").strip()[:60]
        if not name:
            return Response({"detail": "Poné un nombre."}, status=status.HTTP_400_BAD_REQUEST)
        if team.profiles.filter(name__iexact=name).exclude(pk=profile.pk).exists():
            return Response({"detail": "Ya hay un perfil con ese nombre."}, status=status.HTTP_400_BAD_REQUEST)
        profile.name = name
    profile.save()
    changes = {k: {"from": before[k], "to": getattr(profile, k)} for k in before if before[k] != getattr(profile, k)}
    _audit(request, "team.profile_update", profile, profile.name, changes)
    return Response({"id": profile.id, "name": profile.name, "accesses": profile.accesses})


def delete_profile(request, profile):
    in_use = profile.members.exists() or TeamInvitation.objects.filter(
        profile=profile, status=TeamInvitation.Status.PENDING
    ).exists()
    if in_use:
        return Response(
            {"detail": "Hay miembros o invitaciones pendientes con este perfil: cambialos antes de borrarlo."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    _audit(request, "team.profile_update", profile, profile.name, {"deleted": {"from": False, "to": True}})
    profile.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


class ProfileView(_TeamView):
    """PATCH/DELETE profiles/<id>/. Cambiar un perfil cambia los accesos de
    todos los que lo tienen: quien no tiene "Equipo" de dueño no puede meter
    accesos que él no tiene."""

    def patch(self, request, pk):
        profile = get_object_or_404(TeamProfile, pk=pk, team=self.team())
        return update_profile(request, profile, grantable=member_accesses(request.user))

    def delete(self, request, pk):
        return delete_profile(request, get_object_or_404(TeamProfile, pk=pk, team=self.team()))


class TransferView(APIView):
    """POST transfer/ ``{member_id}`` — solo el dueño (lo chequea el servicio)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        team = _current_member(request).team
        target = get_object_or_404(TeamMember, pk=request.data.get("member_id"), team=team)
        try:
            services.transfer_ownership(team, request.user, target)
        except services.TeamError as exc:
            return _error(exc)
        _audit(request, "team.transfer", team, team_name(team), {"owner": {"from": request.user.email, "to": target.user.email}})
        return _user_response(request, owner_email=target.user.email)


class CloseTeamView(APIView):
    """POST close/ ``{confirm, reason}`` — el dueño da de baja el comercio
    (lo chequea el servicio). Nada se borra: ver ``services.close_team``.
    Devuelve el usuario visto desde otro de sus comercios (o sin comercio)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        team = _current_member(request).team
        name = team_name(team)
        try:
            services.close_team(
                team, request.user, confirm=request.data.get("confirm"), reason=request.data.get("reason") or ""
            )
        except services.TeamError as exc:
            return _error(exc)
        _audit(request, "team.close", team, name, {"closed": {"from": False, "to": True}})
        return _user_response(request, team_name=name)


# Lo que hace la gente del comercio con los datos del comercio. Nunca lo de
# la plataforma (logins, usuarios, roles, soporte, catálogos).
ACTIVITY_CATEGORIES = ("orders", "labels", "documents", "integrations", "processing", "team")
# Acciones del comercio que viven en otra categoría: los datos del comercio
# (razón social, CUIT, remitente) se auditan en "users".
ACTIVITY_EXTRA_ACTIONS = ("account.update",)


class TeamActivitySerializer(AuditLogSerializer):
    """La auditoría para el dueño, sin la IP ni el navegador de sus empleados:
    eso es de la plataforma, no del comercio. Suma cómo se ve quien lo hizo
    (``actor``: nombre para mostrar, puesto, foto), leído una vez por página
    en ``TeamActivityView.get_serializer_context``."""

    class Meta(AuditLogSerializer.Meta):
        fields = [field for field in AuditLogSerializer.Meta.fields if field not in ("ip_address", "user_agent")]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["actor"] = self.context.get("people", {}).get(instance.actor_id)
        return data


class TeamActivityView(_TeamView, ListAPIView):
    """GET activity/ — la auditoría filtrada a los miembros ACTUALES del
    comercio, con los mismos filtros que ``/audit/logs/`` (``actor``,
    ``action``, ``search``, ``date_from``/``date_to``) y paginada igual."""

    serializer_class = TeamActivitySerializer
    pagination_class = AuditLogPagination

    def get_serializer_context(self):
        context = super().get_serializer_context()
        members = self.team().members.select_related("user", "user__personal_profile")
        context["people"] = {member.user_id: public_fields(member.user, self.request) for member in members}
        return context

    def get_queryset(self):
        team = self.team()
        business = Q(category__in=ACTIVITY_CATEGORIES) | Q(action__in=ACTIVITY_EXTRA_ACTIONS)
        # Las filas nuevas saben en qué comercio pasaron (``AuditLog.team_id``):
        # una persona que está en varios no mezcla lo de uno con lo de otro.
        here = Q(team_id=team.pk) & business
        # Las de antes (sin ``team_id``): lo que cada miembro ACTUAL hizo desde
        # que está en este comercio (quien viene de otro no trae su historial),
        # más lo que le pasó al comercio aunque quien lo hizo ya no esté.
        since = Q(pk__in=[])
        for user_id, joined_at in team.members.values_list("user_id", "joined_at"):
            since |= Q(actor_id=user_id, created_at__gte=joined_at)
        about_team = Q(category="team", target_type="team", target_id=str(team.pk))
        # Un movimiento "sobre un comercio" (se sumó, lo dejó, se renombró) se
        # muestra solo en el comercio del que habla.
        about_any_team = Q(category="team", target_type="team")
        before = Q(team_id=None) & ((since & business & ~about_any_team) | about_team)
        return filter_logs(self.request.query_params).filter(here | before)


class MyActivityView(ListAPIView):
    """GET activity/mine/ — lo que hizo la persona que pregunta en su comercio
    actual, para cualquier miembro (no hace falta el acceso "Equipo"): un
    empleado puede revisar qué despachó o imprimió. Misma regla que
    ``TeamActivityView`` (desde que se sumó, solo categorías del comercio) y
    los mismos filtros salvo ``actor``, que es siempre él."""

    serializer_class = TeamActivitySerializer
    pagination_class = AuditLogPagination

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("users.me.view")]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["people"] = {self.request.user.pk: public_fields(self.request.user, self.request)}
        return context

    def get_queryset(self):
        member = _current_member(self.request)
        business = Q(category__in=ACTIVITY_CATEGORIES) | Q(action__in=ACTIVITY_EXTRA_ACTIONS)
        params = self.request.query_params.copy()
        params.pop("actor", None)
        here = Q(team_id=member.team_id)
        before = Q(team_id=None) & (~Q(category="team", target_type="team") | Q(target_id=str(member.team_id)))
        # Desde que está (esta vez) en el comercio: si se fue y volvió, lo de
        # antes no es de su membresía actual.
        return filter_logs(params).filter(
            business, here | before, actor=self.request.user, created_at__gte=member.joined_at
        )


# --- Las invitaciones del propio usuario ---------------------------------------


def _invitation_accesses(invitation):
    return sorted(invitation.profile.accesses if invitation.profile_id else invitation.accesses)


class MyInvitationListView(APIView):
    """GET my-invitations/ — cualquier usuario con sesión: es para el invitado."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            [
                {
                    "id": invitation.id,
                    "team_name": team_name(invitation.team),
                    "invited_by": invitation.invited_by.email if invitation.invited_by else "",
                    "profile_name": invitation.profile.name if invitation.profile_id else "",
                    "accesses": _invitation_accesses(invitation),
                    # Las etiquetas, en el orden del catálogo: el invitado
                    # todavía no puede leer GET /team/.
                    "access_labels": [label for key, label in ACCESSES.items() if key in _invitation_accesses(invitation)],
                    "expires_at": invitation.expires_at,
                }
                for invitation in services.pending_invitations_for(request.user)
            ]
        )


class MyInvitationActionView(APIView):
    """POST my-invitations/<id>/accept|decline/"""

    permission_classes = [IsAuthenticated]

    def post(self, request, pk, decision):
        if decision not in ("accept", "decline"):
            return Response(status=status.HTTP_404_NOT_FOUND)
        invitation = get_object_or_404(services.pending_invitations_for(request.user), pk=pk)
        if decision == "decline":
            services.decline(invitation)
            _audit(request, "team.decline", invitation, invitation.email)
            return Response({"status": invitation.status})
        try:
            services.accept(invitation, request.user)
        except services.TeamError as exc:
            return _error(exc)
        _audit(request, "team.join", invitation.team, team_name(invitation.team))
        # Entra al comercio que aceptó: el frontend guarda el usuario de ESE
        # comercio y lo pone como el actual de la pestaña.
        return _user_response(
            request, invitation.team_id, status=invitation.status, team_name=team_name(invitation.team)
        )


# --- Lo de la propia persona: sus comercios ------------------------------------


class CreateTeamView(APIView):
    """POST create/ ``{name}`` — cualquier persona con sesión crea un comercio
    propio (uno más). Devuelve el usuario visto desde el comercio nuevo."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            team = services.create_business(request.user, request.data.get("name"))
        except services.TeamError as exc:
            return _error(exc)
        _audit(request, "team.create", team, team_name(team))
        return _user_response(request, team.id, team={"id": team.id, "display_name": team_name(team)})


class SwitchTeamView(APIView):
    """POST switch/ ``{team_id}`` — pasa a trabajar en otro de sus comercios:
    queda como el último usado y devuelve el usuario visto desde él."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        member = next((m for m in memberships(request.user) if str(m.team_id) == str(request.data.get("team_id"))), None)
        if member is None:
            return Response({"detail": "No sos parte de ese comercio."}, status=status.HTTP_404_NOT_FOUND)
        mark_used(member)
        return _user_response(request, member.team_id)


class LeaveTeamView(APIView):
    """POST leave/ — la persona se va del comercio actual (el dueño no).
    Lo que hizo queda en el comercio."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        member = _current_member(request)
        team = member.team
        try:
            services.leave(member)
        except services.TeamError as exc:
            return _error(exc)
        _audit(request, "team.leave", team, request.user.email)
        return _user_response(request, team_name=team_name(team))


class JoinWithCodeView(APIView):
    """POST join/ ``{code}`` — pide sumarse con el código del comercio. Queda
    pendiente hasta que lo aprueben. Con límite, para que no se puedan
    probar códigos a lo bruto."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "team_join"

    def post(self, request):
        try:
            join_request, created = services.request_join(request.user, request.data.get("code"))
        except services.TeamError as exc:
            return _error(exc)
        if created:
            _audit(request, "team.join_request", join_request.team, request.user.email)
        return Response(
            {"id": join_request.id, "status": join_request.status, "team_name": team_name(join_request.team)},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MyJoinRequestListView(APIView):
    """GET my-join-requests/ — los pedidos pendientes de la persona."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = TeamJoinRequest.objects.filter(
            user=request.user, status=TeamJoinRequest.Status.PENDING, team__closed_at=None
        ).select_related("team__owner")
        return Response([{"id": r.id, "team_name": team_name(r.team), "created_at": r.created_at} for r in rows])


class MyJoinRequestView(APIView):
    """DELETE my-join-requests/<id>/ — la persona cancela su pedido."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        join_request = get_object_or_404(
            TeamJoinRequest, pk=pk, user=request.user, status=TeamJoinRequest.Status.PENDING
        )
        services.cancel_join(join_request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class JoinCodeView(_TeamView):
    """GET/PATCH join-code/ ``{enabled}`` — el código del comercio actual."""

    def get(self, request):
        team = self.team()
        return Response({"code": team.join_code, "enabled": team.join_code_enabled})

    def patch(self, request):
        team = self.team()
        before = team.join_code_enabled
        services.set_join_code_enabled(team, request.data.get("enabled") is not False)
        if before != team.join_code_enabled:
            _audit(request, "team.join_code_update", team, team_name(team), {"enabled": {"from": before, "to": team.join_code_enabled}})
        return Response({"code": team.join_code, "enabled": team.join_code_enabled})


class JoinCodeRegenerateView(_TeamView):
    """POST join-code/regenerate/ — código nuevo: el viejo deja de servir."""

    def post(self, request):
        team = self.team()
        services.regenerate_join_code(team)
        _audit(request, "team.join_code_update", team, team_name(team), {"code": {"from": "***", "to": "***"}})
        return Response({"code": team.join_code, "enabled": team.join_code_enabled})


class JoinRequestDecisionView(_TeamView):
    """POST join-requests/<id>/approve/ ``{profile|accesses, all_stores,
    store_ids}`` y POST join-requests/<id>/reject/."""

    def post(self, request, pk, decision):
        if decision not in ("approve", "reject"):
            return Response(status=status.HTTP_404_NOT_FOUND)
        team = self.team()
        join_request = get_object_or_404(
            TeamJoinRequest.objects.select_related("user"), pk=pk, team=team, status=TeamJoinRequest.Status.PENDING
        )
        try:
            if decision == "reject":
                services.reject_join(join_request, request.user)
                _audit(request, "team.join_reject", join_request.user, join_request.user.email)
                return Response({"status": join_request.status})
            all_stores, store_ids = _store_choice(request.data)
            member = services.approve_join(
                join_request,
                request.user,
                profile=_profile(team, request.data.get("profile")),
                accesses=request.data.get("accesses") or [],
                all_stores=all_stores,
                store_ids=store_ids,
            )
        except services.TeamError as exc:
            return _error(exc)
        _audit(request, "team.join_approve", join_request.user, join_request.user.email)
        return Response(member_data(member, request), status=status.HTTP_201_CREATED)


# --- Admin de la plataforma ----------------------------------------------------


class AdminUserTeamView(APIView):
    """GET/POST /api/v1/users/<id>/team/ — el admin ve y cambia el comercio de
    un usuario, sin invitación (``users.view`` / ``users.edit``)."""

    def get_permissions(self):
        permission = "users.view" if self.request.method == "GET" else "users.edit"
        return [IsAuthenticated(), HasRolePermission(permission)]

    def get(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        return Response(
            {
                "user": {"id": user.id, "email": user.email},
                "teams": [
                    {
                        "id": member.team_id,
                        "name": team_name(member.team),
                        "owner_email": member.team.owner.email if member.team.owner else "",
                        "role": member.role,
                    }
                    for member in memberships(user)
                ],
            }
        )

    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        team = get_object_or_404(Team, pk=request.data.get("team_id"), closed_at=None)
        role = request.data.get("role") or TeamMember.Role.EMPLOYEE
        try:
            member = services.admin_assign(user, team, role)
        except services.TeamError as exc:
            return _error(exc)
        _audit(request, "team.admin_assign", user, user.email, {"team": {"from": None, "to": team_name(team)}, "role": {"from": None, "to": member.role}})
        return Response({"role": member.role, "team": {"id": team.id, "name": team_name(team)}})


class AdminTeamListView(APIView):
    """GET /api/v1/teams/?search= — para que el admin elija un comercio."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("users.view")]

    def get(self, request):
        search = (request.query_params.get("search") or "").strip()
        teams = (
            Team.objects.filter(closed_at=None)
            .select_related("owner")
            .annotate(member_count=Count("members"))
            .order_by("id")
        )
        if search:
            teams = teams.filter(Q(name__icontains=search) | Q(owner__email__icontains=search))
        return Response(
            [
                {
                    "id": t.id,
                    "name": team_name(t),
                    "owner_email": t.owner.email if t.owner else "",
                    "member_count": t.member_count,
                }
                for t in teams[:50]
            ]
        )


class AdminTeamProfilesView(APIView):
    """GET /api/v1/teams/profiles/?search= — los perfiles de cada comercio, de
    solo lectura (pestaña "Perfiles de los comercios" de roles.html). Con el
    catálogo de accesos y qué permisos de rol habilita cada uno: el rol es el
    techo que fija el admin y el acceso del perfil, lo que el dueño reparte
    dentro de ese techo (``permissions_map.TEAM_SCOPED_PERMISSIONS``)."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("users.view")]

    def get(self, request):
        search = (request.query_params.get("search") or "").strip()
        teams = (
            Team.objects.filter(closed_at=None)
            .select_related("owner")
            .annotate(member_count=Count("members", distinct=True))
            .prefetch_related("profiles")
            .order_by("id")
        )
        if search:
            teams = teams.filter(Q(name__icontains=search) | Q(owner__email__icontains=search))
        teams = list(teams[:50])
        profile_members = dict(
            TeamProfile.objects.filter(team__in=teams)
            .annotate(n=Count("members"))
            .values_list("id", "n")
        )
        gated = {}
        for permission, access in TEAM_SCOPED_PERMISSIONS.items():
            gated.setdefault(access, []).append(permission)
        return Response(
            {
                "accesses": [
                    {"key": key, "label": label, "permissions": sorted(gated.get(key, []))}
                    for key, label in ACCESSES.items()
                ],
                "teams": [
                    {
                        "id": team.id,
                        "name": team_name(team),
                        "owner_email": team.owner.email if team.owner else "",
                        "member_count": team.member_count,
                        "profiles": [
                            {
                                "id": profile.id,
                                "name": profile.name,
                                "accesses": profile.accesses,
                                "member_count": profile_members.get(profile.id, 0),
                            }
                            for profile in team.profiles.all()
                        ],
                    }
                    for team in teams
                ],
            }
        )


class AdminTeamProfileCreateView(APIView):
    """POST /api/v1/teams/<team_id>/profiles/ — el admin de la plataforma crea
    un perfil en cualquier comercio (``users.edit``), sin el límite de "no dar
    lo que no tenés": el admin tiene todo. Auditado como en el equipo."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("users.edit")]

    def post(self, request, team_id):
        return create_profile(request, get_object_or_404(Team, pk=team_id), grantable=None)


class AdminTeamProfileView(APIView):
    """PATCH/DELETE /api/v1/teams/profiles/<id>/ — el admin edita o borra el
    perfil de cualquier comercio (``users.edit``). Mismas reglas que el equipo
    (nombre único, en uso no se borra) salvo el límite de accesos."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("users.edit")]

    def patch(self, request, pk):
        return update_profile(request, get_object_or_404(TeamProfile, pk=pk), grantable=None)

    def delete(self, request, pk):
        return delete_profile(request, get_object_or_404(TeamProfile, pk=pk))


class AdminClosedTeamListView(APIView):
    """GET /api/v1/teams/closed/?search= — los comercios dados de baja, con
    su dueño real, cuándo, quién, el motivo y cuántos datos quedaron
    guardados (``users.view``)."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("users.view")]

    def get(self, request):
        search = (request.query_params.get("search") or "").strip()
        teams = (
            Team.objects.exclude(closed_at=None)
            .select_related("owner", "data_user", "closed_by")
            .order_by("-closed_at")
        )
        if search:
            teams = teams.filter(Q(name__icontains=search) | Q(owner__email__icontains=search))
        return Response(
            [
                {
                    "id": team.id,
                    "name": team.name or f"Comercio de {team.owner.email if team.owner else '(usuario borrado)'}",
                    "owner_email": team.owner.email if team.owner else "",
                    "closed_at": team.closed_at,
                    "closed_by": team.closed_by.email if team.closed_by else "",
                    "reason": team.close_reason,
                    "data": services.owned_data_count(team.data_user),
                    "store_count": len((team.closed_state or {}).get("stores") or {}),
                }
                for team in teams[:100]
            ]
        )


class AdminTeamReopenView(APIView):
    """POST /api/v1/teams/<id>/reopen/ — el admin le devuelve el comercio a su
    dueño (``users.edit``)."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("users.edit")]

    def post(self, request, pk):
        team = get_object_or_404(Team, pk=pk)
        try:
            warnings = services.reopen_team(team)
        except services.TeamError as exc:
            return _error(exc)
        team.refresh_from_db()
        _audit(request, "team.reopen", team, team_name(team), {"closed": {"from": True, "to": False}})
        return Response(
            {"id": team.id, "name": team_name(team), "owner_email": team.owner.email if team.owner else "", "warnings": warnings}
        )
