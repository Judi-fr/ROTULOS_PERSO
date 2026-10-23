"""Varios comercios por persona (etapa 1): se arranca sin comercio, se crea
o se suma a varios, cada request trabaja en el de ``X-Team-Id`` y los datos
de cada comercio cuelgan de su usuario de datos."""

from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts import teams
from apps.accounts.models import Team, TeamInvitation, TeamJoinRequest, TeamMember
from apps.audit.models import AuditLog
from apps.integrations.events import enqueue_event, process_due_events
from apps.integrations.models import StoreConnection
from apps.integrations.providers import NormalizedOrder, get_provider
from apps.integrations.stores import make_oauth_state, make_share_token, read_oauth_state, read_share_token
from apps.monitoring import notices
from apps.monitoring.models import MerchantNotice
from apps.orders.models import Address, Order
from apps.printing.services import agent_for_token, create_agent

from .test_team_permissions import bearer, make_user

BASE = "/api/v1/team/"
ORDERS = "/api/v1/orders/"


def in_team(user, team):
    """Headers de una request de ``user`` trabajando en ``team``."""
    return {**bearer(user), "HTTP_X_TEAM_ID": str(team.pk)}


def make_order(team, number="1001"):
    address = Address.objects.create(
        user=team.data_user, recipient_name="Comprador", street="Calle", number="1", city="X", state="Y",
        postal_code="1000", origin=Address.Origin.SHIPMENT,
    )
    return Order.objects.create(user=team.data_user, address=address, external_id=number, external_number=number)


def order_ids(response):
    data = response.data
    rows = data["results"] if isinstance(data, dict) and "results" in data else data
    return [row["id"] for row in rows]


class StartWithoutTeamTests(APITestCase):
    def setUp(self):
        self.person = make_user("nueva@example.com")

    def test_a_new_person_has_no_team_and_none_is_created(self):
        response = self.client.get(ORDERS, **bearer(self.person))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "no_team")
        self.assertIsNone(teams.membership(self.person))
        self.assertFalse(Team.objects.exists())

        me = self.client.get("/api/v1/auth/me/", **bearer(self.person))
        self.assertEqual(me.status_code, 200)
        self.assertIsNone(me.data["team"])
        self.assertEqual(me.data["teams"], [])
        self.assertNotIn("orders.view", me.data["permissions"])

    def test_create_a_team(self):
        response = self.client.post(f"{BASE}create/", {"name": "Librería"}, format="json", **bearer(self.person))
        self.assertEqual(response.status_code, 200, response.data)
        team = Team.objects.get()
        self.assertEqual((team.owner, team.name), (self.person, "Librería"))
        data_user = team.data_user
        self.assertNotEqual(data_user, self.person)
        self.assertFalse(data_user.is_active)
        self.assertFalse(data_user.has_usable_password())
        self.assertTrue(data_user.email.endswith("@datos.invalid"))
        self.assertEqual(response.data["user"]["team"]["id"], team.pk)
        self.assertEqual(response.data["user"]["team"]["role"], "owner")
        self.assertEqual(len(team.join_code), teams.JOIN_CODE_LENGTH)
        self.assertTrue(AuditLog.objects.filter(action="team.create", team_id=team.pk).exists())
        # Ya trabaja en él: lo que crea cuelga de su usuario de datos.
        self.assertEqual(self.client.get(ORDERS, **bearer(self.person)).status_code, 200)
        self.assertEqual(teams.data_owner(self.person), data_user)

    def test_create_needs_a_name(self):
        response = self.client.post(f"{BASE}create/", {"name": "  "}, format="json", **bearer(self.person))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Team.objects.exists())

    def test_data_users_are_not_people_in_the_user_admin(self):
        teams.create_team(self.person, name="Uno")
        admin = make_user("admin@example.com", group="admin")
        response = self.client.get("/api/v1/users/?page_size=100", **bearer(admin))
        self.assertEqual(response.status_code, 200, response.data)
        emails = [row["email"] for row in response.data["results"]]
        self.assertIn(self.person.email, emails)
        self.assertFalse([email for email in emails if email.endswith("@datos.invalid")])
        self.assertEqual(response.data["summary"]["total"], 2)
        data_user = Team.objects.get().data_user
        self.assertEqual(self.client.get(f"/api/v1/users/{data_user.pk}/", **bearer(admin)).status_code, 404)


class TwoTeamsTests(APITestCase):
    def setUp(self):
        self.person = make_user("duena@example.com")
        self.team_a = teams.create_team(self.person, name="A")
        self.team_b = teams.create_team(self.person, name="B")
        self.order_a = make_order(self.team_a, "A-1")
        self.order_b = make_order(self.team_b, "B-1")
        self.stranger = make_user("otra@example.com")
        self.team_c = teams.create_team(self.stranger, name="C")
        self.order_c = make_order(self.team_c, "C-1")

    def test_each_header_sees_only_its_team(self):
        self.assertEqual(order_ids(self.client.get(ORDERS, **in_team(self.person, self.team_a))), [self.order_a.pk])
        self.assertEqual(order_ids(self.client.get(ORDERS, **in_team(self.person, self.team_b))), [self.order_b.pk])
        response = self.client.get(f"{ORDERS}{self.order_a.pk}/", **in_team(self.person, self.team_b))
        self.assertEqual(response.status_code, 404)

    def test_a_forged_header_falls_back_to_her_own_team(self):
        response = self.client.get(ORDERS, **in_team(self.person, self.team_c))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.order_c.pk, order_ids(response))
        self.assertEqual(
            self.client.get(f"{ORDERS}{self.order_c.pk}/", **in_team(self.person, self.team_c)).status_code, 404
        )

    def test_without_header_the_last_used_team_wins(self):
        # B se creó último: es el último usado.
        self.assertEqual(order_ids(self.client.get(ORDERS, **bearer(self.person))), [self.order_b.pk])
        response = self.client.post(f"{BASE}switch/", {"team_id": self.team_a.pk}, format="json", **bearer(self.person))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["user"]["team"]["id"], self.team_a.pk)
        self.assertEqual(order_ids(self.client.get(ORDERS, **bearer(self.person))), [self.order_a.pk])

    def test_cannot_switch_to_someone_elses_team(self):
        response = self.client.post(f"{BASE}switch/", {"team_id": self.team_c.pk}, format="json", **bearer(self.person))
        self.assertEqual(response.status_code, 404)

    def test_payload_lists_every_team_with_its_role(self):
        member = TeamMember.objects.create(team=self.team_c, user=self.person, role=TeamMember.Role.EMPLOYEE)
        me = self.client.get("/api/v1/auth/me/", **in_team(self.person, self.team_c)).data
        self.assertEqual(me["team"]["id"], self.team_c.pk)
        self.assertEqual(me["team"]["role"], "employee")
        self.assertEqual(
            sorted((row["display_name"], row["role"]) for row in me["teams"]),
            [("A", "owner"), ("B", "owner"), ("C", "employee")],
        )
        self.assertEqual(member.team, self.team_c)

    def test_activity_does_not_mix_teams(self):
        self.client.patch(BASE, {"name": "B nuevo"}, format="json", **in_team(self.person, self.team_b))
        in_b = self.client.get(f"{BASE}activity/", **in_team(self.person, self.team_b)).data["results"]
        in_a = self.client.get(f"{BASE}activity/", **in_team(self.person, self.team_a)).data["results"]
        self.assertIn("team.update", [row["action"] for row in in_b])
        self.assertNotIn("team.update", [row["action"] for row in in_a])
        mine_a = self.client.get(f"{BASE}activity/mine/", **in_team(self.person, self.team_a)).data["results"]
        self.assertNotIn("team.update", [row["action"] for row in mine_a])


class InvitationAddsATeamTests(APITestCase):
    def test_accepting_keeps_the_other_teams_and_their_data(self):
        owner = make_user("owner@example.com")
        team = teams.create_team(owner, name="Invita")
        guest = make_user("guest@example.com")
        own = teams.create_team(guest, name="Propio")
        own_order = make_order(own)
        response = self.client.post(f"{BASE}invitations/", {"email": guest.email}, format="json", **bearer(owner))
        self.assertEqual(response.status_code, 201, response.data)
        invitation = TeamInvitation.objects.get()

        response = self.client.post(f"{BASE}my-invitations/{invitation.pk}/accept/", **bearer(guest))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["user"]["team"]["id"], team.pk)
        self.assertEqual(
            set(TeamMember.objects.filter(user=guest).values_list("team_id", flat=True)), {team.pk, own.pk}
        )
        own_order.refresh_from_db()
        self.assertEqual(own_order.user, own.data_user)
        self.assertEqual(order_ids(self.client.get(ORDERS, **in_team(guest, own))), [own_order.pk])


class JoinCodeTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("owner@example.com")
        self.team = teams.create_team(self.owner, name="Con código")
        self.person = make_user("quiere@example.com")

    def join(self, code, user=None):
        return self.client.post(f"{BASE}join/", {"code": code}, format="json", **bearer(user or self.person))

    def test_request_stays_pending_until_approved(self):
        code = self.team.join_code
        response = self.join(f"{code[:4].lower()}-{code[4:].lower()}")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["team_name"], "Con código")
        self.assertEqual(self.join(code).status_code, 200)  # el mismo pedido
        self.assertEqual(TeamJoinRequest.objects.count(), 1)
        self.assertFalse(TeamMember.objects.filter(user=self.person).exists())
        self.assertEqual(self.client.get(ORDERS, **bearer(self.person)).data["code"], "no_team")

        team_data = self.client.get(BASE, **bearer(self.owner)).data
        self.assertEqual([row["email"] for row in team_data["join_requests"]], [self.person.email])
        self.assertEqual(team_data["join_code"], {"code": code, "enabled": True})

        request_id = team_data["join_requests"][0]["id"]
        deposito = self.team.profiles.get(name="Depósito")
        response = self.client.post(
            f"{BASE}join-requests/{request_id}/approve/", {"profile": deposito.pk}, format="json", **bearer(self.owner)
        )
        self.assertEqual(response.status_code, 201, response.data)
        member = TeamMember.objects.get(user=self.person)
        self.assertEqual((member.team, member.role, member.profile), (self.team, "employee", deposito))
        self.assertEqual(TeamJoinRequest.objects.get().status, "approved")
        self.assertEqual(self.client.get(ORDERS, **bearer(self.person)).status_code, 200)

    def test_reject(self):
        self.join(self.team.join_code)
        join_request = TeamJoinRequest.objects.get()
        response = self.client.post(f"{BASE}join-requests/{join_request.pk}/reject/", **bearer(self.owner))
        self.assertEqual(response.status_code, 200)
        join_request.refresh_from_db()
        self.assertEqual(join_request.status, "rejected")
        self.assertFalse(TeamMember.objects.filter(user=self.person).exists())

    def test_the_person_cancels_her_request(self):
        self.join(self.team.join_code)
        mine = self.client.get(f"{BASE}my-join-requests/", **bearer(self.person)).data
        self.assertEqual([row["team_name"] for row in mine], ["Con código"])
        response = self.client.delete(f"{BASE}my-join-requests/{mine[0]['id']}/", **bearer(self.person))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(TeamJoinRequest.objects.get().status, "cancelled")

    def test_wrong_and_disabled_codes_say_the_same(self):
        wrong = self.join("NOEXISTE00")
        self.assertEqual(wrong.status_code, 400)
        self.client.patch(f"{BASE}join-code/", {"enabled": False}, format="json", **bearer(self.owner))
        disabled = self.join(self.team.join_code)
        self.assertEqual(disabled.status_code, 400)
        self.assertEqual(wrong.data["detail"], disabled.data["detail"])

    def test_regenerate_kills_the_old_code(self):
        old = self.team.join_code
        response = self.client.post(f"{BASE}join-code/regenerate/", **bearer(self.owner))
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.data["code"], old)
        self.assertEqual(self.join(old).status_code, 400)
        self.assertEqual(self.join(response.data["code"]).status_code, 201)
        self.assertTrue(AuditLog.objects.filter(action="team.join_code_update").exists())

    def test_approving_cannot_grant_more_than_you_have(self):
        helper = make_user("ayuda@example.com")
        TeamMember.objects.create(
            team=self.team, user=helper, role=TeamMember.Role.EMPLOYEE, accesses=["team", "orders.view"]
        )
        self.join(self.team.join_code)
        join_request = TeamJoinRequest.objects.get()
        todo = self.team.profiles.get(name="Todo")
        response = self.client.post(
            f"{BASE}join-requests/{join_request.pk}/approve/", {"profile": todo.pk}, format="json", **bearer(helper)
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(TeamMember.objects.filter(user=self.person).exists())

    def test_codes_cannot_be_tried_by_brute_force(self):
        with patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"team_join": "3/hour"}):
            statuses = [self.join(f"MAL{i:07d}").status_code for i in range(4)]
        self.assertEqual(statuses, [400, 400, 400, 429])


class LeaveTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.team = teams.create_team(self.owner, name="Del que se va")
        self.employee = make_user("emple@example.com")
        TeamMember.objects.create(team=self.team, user=self.employee, role=TeamMember.Role.EMPLOYEE)

    def test_an_employee_leaves_and_her_work_stays(self):
        order = make_order(self.team)
        response = self.client.post(f"{BASE}leave/", **in_team(self.employee, self.team))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["user"]["team"])
        self.assertFalse(TeamMember.objects.filter(user=self.employee).exists())
        self.assertTrue(Order.objects.filter(pk=order.pk, user=self.team.data_user).exists())
        self.assertTrue(AuditLog.objects.filter(action="team.leave", team_id=self.team.pk).exists())

    def test_the_owner_cannot_leave(self):
        response = self.client.post(f"{BASE}leave/", **in_team(self.owner, self.team))
        self.assertEqual(response.status_code, 400)
        self.assertTrue(TeamMember.objects.filter(user=self.owner, role="owner").exists())


class TransferTests(APITestCase):
    def test_transfer_changes_roles_but_not_the_data(self):
        owner = make_user("owner@example.com")
        team = teams.create_team(owner, name="Pasa")
        heir = make_user("heir@example.com")
        member = TeamMember.objects.create(team=team, user=heir, role=TeamMember.Role.EMPLOYEE)
        order = make_order(team)
        response = self.client.post(f"{BASE}transfer/", {"member_id": member.pk}, format="json", **bearer(owner))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["user"]["team"]["role"], "employee")
        team.refresh_from_db()
        self.assertEqual(team.owner, heir)
        order.refresh_from_db()
        self.assertEqual(order.user, team.data_user)
        self.assertEqual(TeamMember.objects.get(team=team, user=owner).profile.name, "Todo")


class CloseAndReopenTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.team = teams.create_team(self.owner, name="Se cierra")
        self.other = teams.create_team(self.owner, name="Sigue")
        self.employee = make_user("emple@example.com")
        TeamMember.objects.create(team=self.team, user=self.employee, role=TeamMember.Role.EMPLOYEE)
        self.order = make_order(self.team)
        self.store = StoreConnection.objects.create(
            owner=self.team.data_user, platform="tiendanube", external_store_id="555", name="Tienda",
            status=StoreConnection.Status.ACTIVE,
        )

    def close(self):
        return self.client.post(f"{BASE}close/", {"confirm": "BAJA"}, format="json", **in_team(self.owner, self.team))

    def test_close_keeps_the_data_and_the_people_keep_their_other_teams(self):
        response = self.close()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["user"]["team"]["id"], self.other.pk)
        self.team.refresh_from_db()
        self.assertTrue(self.team.is_closed)
        self.assertEqual(self.team.owner, self.owner)
        self.assertFalse(self.team.join_code_enabled)
        self.assertFalse(self.team.members.exists())
        self.order.refresh_from_db()
        self.assertEqual(self.order.user, self.team.data_user)
        self.store.refresh_from_db()
        self.assertEqual(self.store.external_store_id, f"555#baja-{self.team.pk}")
        self.assertEqual(self.store.status, StoreConnection.Status.REVOKED)
        # La empleada no tenía otro comercio: queda sin ninguno (no se le crea uno).
        self.assertIsNone(teams.membership(self.employee))
        self.assertEqual(Team.objects.filter(owner=self.employee).count(), 0)

    def test_reopen_gives_it_back_as_one_more_team(self):
        self.close()
        make_order(self.other, "otro")  # el dueño tiene otro comercio con datos: no importa
        admin = make_user("admin@example.com", group="admin")
        response = self.client.post(f"/api/v1/teams/{self.team.pk}/reopen/", **bearer(admin))
        self.assertEqual(response.status_code, 200, response.data)
        self.team.refresh_from_db()
        self.assertFalse(self.team.is_closed)
        self.assertEqual(TeamMember.objects.get(team=self.team).user, self.owner)
        self.store.refresh_from_db()
        self.assertEqual(self.store.external_store_id, "555")
        self.assertEqual(order_ids(self.client.get(ORDERS, **in_team(self.owner, self.team))), [self.order.pk])

    def test_closed_list_counts_the_data(self):
        self.close()
        admin = make_user("admin@example.com", group="admin")
        rows = self.client.get("/api/v1/teams/closed/", **bearer(admin)).data
        self.assertEqual(rows[0]["owner_email"], self.owner.email)
        self.assertEqual(rows[0]["data"].get("pedido(s)"), 1)


class DataUserIsNotAPersonTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.team = teams.create_team(self.owner, name="Negocio")

    def test_oauth_state_and_share_link_carry_the_team(self):
        state = make_oauth_state(self.team.data_user, "tiendanube")
        self.assertEqual(read_oauth_state(state, "tiendanube"), self.team.data_user)
        token = make_share_token(self.team.data_user, "tiendanube")
        self.assertEqual(read_share_token(token, "tiendanube"), self.team.data_user)
        self.team.closed_at = timezone.now()
        self.team.save()
        with self.assertRaises(Exception):
            read_oauth_state(state, "tiendanube")

    def test_print_agent_of_a_team_authenticates(self):
        agent, token = create_agent(self.team.data_user, "PC del depósito")
        self.assertEqual(agent_for_token(token), agent)

    @override_settings(NOTICES_MAIL_ENABLED=True)
    def test_notices_are_mailed_to_the_owner_person(self):
        MerchantNotice.objects.create(
            owner=self.team.data_user, key="store:1", kind="store", title="Tienda desconectada", access="stores"
        )
        notices._mail_new(timezone.now() + timedelta(seconds=1))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.owner.email])
        self.assertIn("«Negocio»", mail.outbox[0].body)


class WorkerTests(TestCase):
    def test_store_orders_hang_from_the_team_data_user(self):
        owner = make_user("owner@example.com")
        team = teams.create_team(owner, name="Con tienda")
        store = StoreConnection.objects.create(
            owner=team.data_user, platform="tiendanube", external_store_id="777", name="Tienda",
            status=StoreConnection.Status.ACTIVE,
        )
        provider = get_provider("tiendanube")
        event_type = provider.order_sync_events[0]
        normalized = NormalizedOrder(
            external_id="9001", external_number="9001", recipient_name="Compradora", street="Calle", number="1",
            city="X", state="Y", postal_code="1000",
        )
        enqueue_event(platform="tiendanube", event_type=event_type, connection=store, resource_id="9001",
                      payload={"id": 9001})
        with patch.object(type(provider), "get_order", return_value={"id": 9001}), patch.object(
            type(provider), "normalize_order", return_value=normalized
        ):
            process_due_events()
        order = Order.objects.get(external_id="9001")
        self.assertEqual(order.user, team.data_user)
        self.assertEqual(order.store_connection, store)
