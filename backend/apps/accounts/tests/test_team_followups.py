"""Pendientes de los comercios con equipo (2026-10-20): límite de
invitaciones, mail fallido, invitaciones vencidas, planilla con pedidos de
otra tienda y una sola consulta de membresía por request."""

from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.db import IntegrityError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts import teams
from apps.accounts.models import TeamInvitation, TeamMember
from apps.orders.models import Address, Order

from .test_store_scope import store
from .test_team_permissions import add_employee, bearer, make_owner, make_user

INVITE = "/api/v1/team/invitations/"


class InvitationFollowupTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_owner("owner@example.com")
        self.team = teams.team_for(self.owner)
        self.profile = self.team.profiles.get(name="Depósito")

    def invite(self, email):
        return self.client.post(INVITE, {"email": email, "profile": self.profile.id}, format="json", **bearer(self.owner))

    def test_invitations_are_rate_limited(self):
        # El contador del límite vive en la caché, que es del proceso: sin
        # limpiarla, los tests siguientes que invitan (con usuarios del mismo
        # id) heredarían el límite agotado.
        self.addCleanup(cache.clear)
        codes = [self.invite("no-es-un-mail").status_code for _ in range(32)]
        self.assertEqual(codes[0], 400)
        self.assertEqual(codes[-1], 429)

    def test_a_failed_mail_leaves_no_invitation(self):
        with mock.patch("apps.accounts.team_services.send_mail", side_effect=OSError("SMTP caído")):
            response = self.invite("e@example.com")
        self.assertEqual(response.status_code, 502)
        self.assertIn("No se pudo mandar el mail", response.data["detail"])
        self.assertFalse(TeamInvitation.objects.exists())
        # Y se puede volver a intentar sin "ya hay una invitación pendiente".
        self.assertEqual(self.invite("e@example.com").status_code, 201)

    def test_a_failed_resend_says_so(self):
        self.invite("e@example.com")
        invitation = TeamInvitation.objects.get()
        with mock.patch("apps.accounts.team_services.send_mail", side_effect=OSError("SMTP caído")):
            response = self.client.post(f"{INVITE}{invitation.id}/resend/", **bearer(self.owner))
        self.assertEqual(response.status_code, 502)

    def test_expired_invitations_are_not_listed_as_pending(self):
        self.invite("vieja@example.com")
        TeamInvitation.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.invite("nueva@example.com")
        emails = [row["email"] for row in self.client.get("/api/v1/team/", **bearer(self.owner)).data["invitations"]]
        self.assertEqual(emails, ["nueva@example.com"])


class ManifestScopeTests(APITestCase):
    def test_manifest_with_an_order_of_another_store_is_refused(self):
        owner = make_owner("owner@example.com")
        data = teams.data_owner(owner)
        t1, t2 = store(data, "1"), store(data, "2")
        address = Address.objects.create(user=data, street="C", number="1", city="CABA")
        o1 = Order.objects.create(user=data, address=address, store_connection=t1, external_id="1")
        o2 = Order.objects.create(user=data, address=address, store_connection=t2, external_id="2")
        limited, member = add_employee(owner, "limitado@example.com", profile="Todo")
        member.all_stores = False
        member.save()
        member.stores.set([t1])
        response = self.client.post("/api/v1/orders/manifest/", {"order_ids": [o1.id, o2.id]}, format="json", **bearer(limited))
        self.assertEqual(response.status_code, 400)
        self.assertIn("tiendas que no tenés habilitadas", str(response.data))
        response = self.client.post("/api/v1/orders/manifest/", {"order_ids": [o1.id]}, format="json", **bearer(limited))
        self.assertEqual(response.status_code, 200)


class MembershipCacheTests(TestCase):
    def test_membership_is_read_once_per_request(self):
        user = make_owner("u@example.com")
        with teams.request_cache():
            with CaptureQueriesContext(connection) as queries:
                for _ in range(5):
                    teams.membership(user)
                    teams.data_owner(user)
        self.assertEqual(len(queries), 1)

    def test_without_a_request_nothing_is_cached(self):
        user = make_owner("u@example.com")
        with CaptureQueriesContext(connection) as queries:
            teams.membership(user)
            teams.membership(user)
        self.assertEqual(len(queries), 2)

    def test_a_change_inside_the_request_is_seen(self):
        owner = make_owner("owner@example.com")
        employee = make_user("e@example.com")
        with teams.request_cache():
            self.assertIsNone(teams.membership(employee))
            TeamMember.objects.create(team=teams.team_for(owner), user=employee, role=TeamMember.Role.EMPLOYEE)
            teams.forget(employee)
            self.assertEqual(teams.data_owner(employee), teams.team_for(owner).data_user)

    def test_api_requests_use_the_cache(self):
        from django.conf import settings

        self.assertIn("apps.accounts.teams.TeamCacheMiddleware", settings.MIDDLEWARE)


class TeamMinorFollowupTests(APITestCase):
    """Los cuatro menores que quedaron de la revisión (2026-10-20)."""

    def setUp(self):
        cache.clear()
        self.owner = make_owner("owner@example.com")
        self.team = teams.team_for(self.owner)

    def test_creating_a_profile_respects_no_escalation(self):
        manager, _ = add_employee(self.owner, "m@example.com", accesses=["team", "orders.view"])
        body = {"name": "Grande", "accesses": ["orders.view", "stores"]}
        response = self.client.post("/api/v1/team/profiles/", body, format="json", **bearer(manager))
        self.assertEqual(response.status_code, 403)
        body["accesses"] = ["orders.view"]
        response = self.client.post("/api/v1/team/profiles/", body, format="json", **bearer(manager))
        self.assertEqual(response.status_code, 201, response.data)

    def test_member_patch_keeps_what_was_not_sent(self):
        t1 = store(self.team.data_user, "1")
        _, member = add_employee(self.owner, "e@example.com", profile="Depósito")
        response = self.client.patch(
            f"/api/v1/team/members/{member.id}/", {"all_stores": False, "store_ids": [t1.id]}, format="json", **bearer(self.owner)
        )
        self.assertEqual(response.status_code, 200, response.data)
        member.refresh_from_db()
        self.assertEqual(member.profile.name, "Depósito")
        self.assertEqual(teams.allowed_store_ids(member.user), {t1.id})
        # Y al revés: cambiar el perfil no toca las tiendas.
        atencion = self.team.profiles.get(name="Atención")
        self.client.patch(f"/api/v1/team/members/{member.id}/", {"profile": atencion.id}, format="json", **bearer(self.owner))
        member.refresh_from_db()
        self.assertEqual((member.profile.name, member.all_stores), ("Atención", False))

    def test_leaving_shows_in_the_business_she_left(self):
        employee, _ = add_employee(self.owner, "e@example.com", profile="Todo")
        self.assertEqual(self.client.post("/api/v1/team/leave/", **bearer(employee)).status_code, 200)
        rows = self.client.get("/api/v1/team/activity/", **bearer(self.owner)).data["results"]
        self.assertIn(("e@example.com", "team.leave"), [(row["actor_email"], row["action"]) for row in rows])

    def test_business_data_changes_show_in_the_activity(self):
        employee, _ = add_employee(self.owner, "e@example.com", profile="Todo")
        response = self.client.patch("/api/v1/auth/me/account/", {"legal_name": "Mi SRL"}, format="json", **bearer(employee))
        self.assertEqual(response.status_code, 200, response.data)
        rows = self.client.get("/api/v1/team/activity/", **bearer(self.owner)).data["results"]
        self.assertIn(("e@example.com", "account.update"), [(row["actor_email"], row["action"]) for row in rows])
