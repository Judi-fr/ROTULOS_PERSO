"""API del equipo: miembros, perfiles, invitaciones, titularidad y asignación
por el admin."""

from datetime import timedelta

from django.core import mail
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.accounts import teams
from apps.accounts.models import EmailVerification, Team, TeamInvitation, TeamMember
from apps.audit.models import AuditLog
from apps.integrations.models import StoreConnection
from apps.labels.models import LabelTemplate
from apps.orders.models import Address, Order

from .test_team_permissions import add_employee, bearer, make_user

BASE = "/api/v1/team/"


def unverified(user):
    EmailVerification.objects.create(user=user, token=f"t-{user.pk}", is_verified=False)


class TeamApiTestCase(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.team = teams.team_for(self.owner)
        self.deposito = self.team.profiles.get(name="Depósito")

    def post(self, url, body=None, user=None):
        return self.client.post(url, body or {}, format="json", **bearer(user or self.owner))

    def invite(self, email, user=None, **extra):
        return self.post(f"{BASE}invitations/", {"email": email, "profile": self.deposito.id, **extra}, user)

    def my_invitation(self, user):
        response = self.client.get(f"{BASE}my-invitations/", **bearer(user))
        self.assertEqual(response.status_code, 200, response.data)
        return response.data


class InvitationTests(TeamApiTestCase):
    def test_owner_invites_and_existing_user_accepts(self):
        employee = make_user("e@example.com")
        response = self.invite("E@example.com")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("invitaciones.html", mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ["e@example.com"])

        pending = self.my_invitation(employee)
        self.assertEqual(len(pending), 1)
        self.assertEqual(self.post(f"{BASE}my-invitations/{pending[0]['id']}/accept/", user=employee).status_code, 200)

        member = TeamMember.objects.get(user=employee)
        self.assertEqual((member.team, member.role, member.profile), (self.team, "employee", self.deposito))
        self.assertFalse(Team.objects.filter(owner=employee).exists())
        self.assertEqual(teams.data_owner(employee), self.owner)

    def test_invitation_for_unregistered_email_waits_for_registration(self):
        self.assertEqual(self.invite("nuevo@example.com").status_code, 201)
        newcomer = make_user("nuevo@example.com")
        pending = self.my_invitation(newcomer)
        self.assertEqual([item["team_name"] for item in pending], [self.team.name or "Comercio de owner@example.com"])

    def test_unverified_or_other_email_cannot_accept(self):
        self.invite("e@example.com")
        invitation = TeamInvitation.objects.get()
        other = make_user("otro@example.com")
        self.assertEqual(self.post(f"{BASE}my-invitations/{invitation.id}/accept/", user=other).status_code, 404)
        employee = make_user("e@example.com")
        unverified(employee)
        response = self.post(f"{BASE}my-invitations/{invitation.id}/accept/", user=employee)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(teams.data_owner(employee), employee)

    def test_user_with_data_cannot_join(self):
        employee = make_user("e@example.com")
        StoreConnection.objects.create(
            owner=employee, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="99", name="Mi tienda"
        )
        self.invite("e@example.com")
        invitation = TeamInvitation.objects.get()
        response = self.post(f"{BASE}my-invitations/{invitation.id}/accept/", user=employee)
        self.assertEqual(response.status_code, 400)
        self.assertIn("tienda", str(response.data["detail"]))
        self.assertEqual(teams.data_owner(employee), employee)

    def test_expired_invitation_cannot_be_accepted(self):
        employee = make_user("e@example.com")
        self.invite("e@example.com")
        TeamInvitation.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.my_invitation(employee), [])
        invitation = TeamInvitation.objects.get()
        self.assertEqual(self.post(f"{BASE}my-invitations/{invitation.id}/accept/", user=employee).status_code, 404)

    def test_decline_and_cancel(self):
        employee = make_user("e@example.com")
        self.invite("e@example.com")
        invitation = TeamInvitation.objects.get()
        self.assertEqual(self.post(f"{BASE}my-invitations/{invitation.id}/decline/", user=employee).status_code, 200)
        self.assertEqual(TeamInvitation.objects.get().status, "declined")
        self.invite("e@example.com")
        second = TeamInvitation.objects.get(status="pending")
        self.assertEqual(self.client.delete(f"{BASE}invitations/{second.id}/", **bearer(self.owner)).status_code, 204)
        self.assertEqual(TeamInvitation.objects.get(pk=second.pk).status, "cancelled")

    def test_resend_sends_the_mail_again(self):
        self.invite("e@example.com")
        invitation = TeamInvitation.objects.get()
        self.assertEqual(self.post(f"{BASE}invitations/{invitation.id}/resend/").status_code, 200)
        self.assertEqual(len(mail.outbox), 2)


class MemberTests(TeamApiTestCase):
    def test_overview_lists_members_profiles_and_catalog(self):
        add_employee(self.owner, "e@example.com", profile="Depósito")
        data = self.client.get(BASE, **bearer(self.owner)).data
        self.assertEqual([m["role"] for m in data["members"]], ["owner", "employee"])
        self.assertEqual({p["name"] for p in data["profiles"]}, {"Todo", "Depósito", "Atención"})
        self.assertEqual(set(data["accesses"]), set(teams.ACCESSES))

    def test_rename_team(self):
        response = self.client.patch(BASE, {"name": "Mi Comercio"}, format="json", **bearer(self.owner))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Team.objects.get(pk=self.team.pk).name, "Mi Comercio")

    def test_no_escalation(self):
        manager, _ = add_employee(self.owner, "m@example.com", accesses=["team", "orders.view"])
        other, other_member = add_employee(self.owner, "o@example.com", accesses=["orders.view"])
        owner_member = teams.membership(self.owner)
        # Invitar con un acceso que no tiene.
        self.assertEqual(
            self.post(f"{BASE}invitations/", {"email": "x@example.com", "accesses": ["stores"]}, manager).status_code, 403
        )
        # Darle a otro algo que no tiene.
        response = self.client.patch(
            f"{BASE}members/{other_member.id}/", {"profile": None, "accesses": ["stores"]}, format="json", **bearer(manager)
        )
        self.assertEqual(response.status_code, 403)
        # Tocarse a sí mismo o al dueño.
        manager_member = teams.membership(manager)
        for member in (manager_member, owner_member):
            with self.subTest(member=member.user.email):
                self.assertEqual(
                    self.client.patch(
                        f"{BASE}members/{member.id}/", {"accesses": ["orders.view"]}, format="json", **bearer(manager)
                    ).status_code,
                    403,
                )
                self.assertEqual(self.client.delete(f"{BASE}members/{member.id}/", **bearer(manager)).status_code, 403)
        # Lo que sí puede: darle a otro lo que él tiene.
        response = self.client.patch(
            f"{BASE}members/{other_member.id}/", {"profile": None, "accesses": ["orders.view", "team"]}, format="json", **bearer(manager)
        )
        self.assertEqual(response.status_code, 200, response.data)

    def test_employee_without_team_access_gets_403(self):
        employee, _ = add_employee(self.owner, "e@example.com", profile="Depósito")
        self.assertEqual(self.client.get(BASE, **bearer(employee)).status_code, 403)

    def test_member_limited_to_stores_of_the_team(self):
        store = StoreConnection.objects.create(
            owner=self.owner, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="1", name="T1"
        )
        foreign = StoreConnection.objects.create(
            owner=make_user("ajeno@example.com"), platform=StoreConnection.Platform.TIENDANUBE, external_store_id="2"
        )
        _, member = add_employee(self.owner, "e@example.com", profile="Todo")
        url = f"{BASE}members/{member.id}/"
        self.assertEqual(
            self.client.patch(url, {"all_stores": False, "store_ids": [foreign.id]}, format="json", **bearer(self.owner)).status_code,
            400,
        )
        response = self.client.patch(url, {"all_stores": False, "store_ids": [store.id]}, format="json", **bearer(self.owner))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(teams.allowed_store_ids(member.user), {store.id})

    def test_removed_employee_starts_empty_and_data_stays(self):
        employee, member = add_employee(self.owner, "e@example.com", profile="Todo")
        address = Address.objects.create(user=self.owner, street="C", number="1", city="CABA")
        order = Order.objects.create(user=self.owner, address=address)
        self.assertEqual(self.client.delete(f"{BASE}members/{member.id}/", **bearer(self.owner)).status_code, 204)
        self.assertEqual(teams.data_owner(employee), employee)
        self.assertEqual(Order.objects.get(pk=order.pk).user, self.owner)


class ProfileTests(TeamApiTestCase):
    def test_profile_crud_and_in_use_cannot_be_deleted(self):
        created = self.post(f"{BASE}profiles/", {"name": "Caja", "accesses": ["orders.view", "inventado"]})
        self.assertEqual(created.status_code, 400)
        created = self.post(f"{BASE}profiles/", {"name": "Caja", "accesses": ["orders.view"]})
        self.assertEqual(created.status_code, 201, created.data)
        add_employee(self.owner, "e@example.com", profile="Depósito")
        self.assertEqual(self.client.delete(f"{BASE}profiles/{self.deposito.id}/", **bearer(self.owner)).status_code, 400)
        self.assertEqual(self.client.delete(f"{BASE}profiles/{created.data['id']}/", **bearer(self.owner)).status_code, 204)


class TransferTests(TeamApiTestCase):
    def test_transfer_moves_every_owned_row(self):
        new_owner, member = add_employee(self.owner, "nuevo@example.com", profile="Todo")
        store = StoreConnection.objects.create(
            owner=self.owner, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="1", name="T1"
        )
        address = Address.objects.create(user=self.owner, street="C", number="1", city="CABA")
        order = Order.objects.create(user=self.owner, address=address, store_connection=store, external_id="1")
        template = LabelTemplate.objects.create(owner=self.owner, name="P", width_cm=10, height_cm=15, design={})

        self.assertEqual(self.post(f"{BASE}transfer/", {"member_id": member.id}, new_owner).status_code, 403)
        response = self.post(f"{BASE}transfer/", {"member_id": member.id})
        self.assertEqual(response.status_code, 200, response.data)

        self.assertEqual(Team.objects.get(pk=self.team.pk).owner, new_owner)
        for obj, field in ((store, "owner"), (address, "user"), (order, "user"), (template, "owner")):
            obj.refresh_from_db()
            self.assertEqual(getattr(obj, field), new_owner, obj)
        old = teams.membership(self.owner)
        self.assertEqual((old.role, old.profile.name), ("employee", "Todo"))
        self.assertEqual(teams.data_owner(self.owner), new_owner)


class AdminAssignTests(TeamApiTestCase):
    def setUp(self):
        super().setUp()
        self.admin = make_user("admin@example.com", group="admin")
        self.admin.is_staff = True
        self.admin.save()

    def test_admin_assigns_without_acceptance(self):
        user = make_user("u@example.com")
        response = self.post(f"/api/v1/users/{user.id}/team/", {"team_id": self.team.id, "role": "employee"}, self.admin)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(teams.data_owner(user), self.owner)
        self.assertEqual(self.client.get(f"/api/v1/users/{user.id}/team/", **bearer(self.admin)).data["team"]["id"], self.team.id)

    def test_admin_cannot_merge_teams_with_data(self):
        user = make_user("u@example.com")
        Address.objects.create(user=user, street="C", number="1", city="CABA")
        response = self.post(f"/api/v1/users/{user.id}/team/", {"team_id": self.team.id, "role": "employee"}, self.admin)
        self.assertEqual(response.status_code, 400)

    def test_merchant_cannot_use_admin_assign(self):
        user = make_user("u@example.com")
        response = self.post(f"/api/v1/users/{user.id}/team/", {"team_id": self.team.id, "role": "employee"})
        self.assertEqual(response.status_code, 403)


class AuditTests(TeamApiTestCase):
    def test_every_team_change_is_audited(self):
        employee = make_user("e@example.com")
        self.invite("e@example.com")
        invitation = TeamInvitation.objects.get()
        self.post(f"{BASE}my-invitations/{invitation.id}/accept/", user=employee)
        member = teams.membership(employee)
        self.client.patch(f"{BASE}members/{member.id}/", {"profile": None, "accesses": ["orders.view"]}, format="json", **bearer(self.owner))
        self.post(f"{BASE}profiles/", {"name": "Caja", "accesses": ["orders.view"]})
        self.client.delete(f"{BASE}members/{member.id}/", **bearer(self.owner))
        actions = list(AuditLog.objects.filter(category="team").order_by("id").values_list("action", "actor__email"))
        self.assertEqual(
            actions,
            [
                ("team.invite", "owner@example.com"),
                ("team.join", "e@example.com"),
                ("team.member_update", "owner@example.com"),
                ("team.profile_update", "owner@example.com"),
                ("team.member_remove", "owner@example.com"),
            ],
        )


class ActivityTests(TeamApiTestCase):
    def test_activity_shows_team_members_actions_only(self):
        employee, _ = add_employee(self.owner, "e@example.com", profile="Todo")
        stranger = make_user("ajeno@example.com")
        AuditLog.objects.create(actor=employee, actor_email=employee.email, category="orders", action="order.ship", target_repr="#1")
        AuditLog.objects.create(actor=employee, actor_email=employee.email, category="auth", action="auth.login_success")
        AuditLog.objects.create(actor=stranger, actor_email=stranger.email, category="orders", action="order.ship", target_repr="#2")
        response = self.client.get(f"{BASE}activity/", **bearer(self.owner))
        self.assertEqual(response.status_code, 200, response.data)
        rows = [(row["actor_email"], row["action"]) for row in response.data["results"]]
        self.assertEqual(rows, [("e@example.com", "order.ship")])

    def test_activity_filters_by_member(self):
        employee, _ = add_employee(self.owner, "e@example.com", profile="Todo")
        AuditLog.objects.create(actor=employee, actor_email=employee.email, category="orders", action="order.ship")
        AuditLog.objects.create(actor=self.owner, actor_email=self.owner.email, category="labels", action="label.batch")
        response = self.client.get(f"{BASE}activity/?actor={employee.id}", **bearer(self.owner))
        self.assertEqual([row["action"] for row in response.data["results"]], ["order.ship"])

    def test_activity_requires_team_access(self):
        employee, _ = add_employee(self.owner, "dep@example.com", profile="Depósito")
        self.assertEqual(self.client.get(f"{BASE}activity/", **bearer(employee)).status_code, 403)


class FrontendContractTests(TeamApiTestCase):
    def test_activity_does_not_expose_ip_or_browser(self):
        employee, _ = add_employee(self.owner, "e@example.com", profile="Todo")
        AuditLog.objects.create(
            actor=employee, actor_email=employee.email, category="orders", action="order.ship",
            ip_address="10.0.0.1", user_agent="Firefox",
        )
        row = self.client.get(f"{BASE}activity/", **bearer(self.owner)).data["results"][0]
        self.assertNotIn("ip_address", row)
        self.assertNotIn("user_agent", row)
        self.assertEqual(row["action_label"], "Envío de pedido actualizado")

    def test_my_invitations_bring_access_labels(self):
        employee = make_user("e@example.com")
        self.invite("e@example.com")
        row = self.my_invitation(employee)[0]
        self.assertEqual(row["access_labels"], ["Ver pedidos", "Imprimir rótulos", "Despachar"])

    def test_accept_returns_the_updated_user(self):
        employee = make_user("e@example.com")
        self.invite("e@example.com")
        invitation = TeamInvitation.objects.get()
        data = self.post(f"{BASE}my-invitations/{invitation.id}/accept/", user=employee).data
        self.assertEqual(data["user"]["team"]["role"], "employee")
        self.assertIn("labels.batch", data["user"]["permissions"])
        self.assertNotIn("stores.manage", data["user"]["permissions"])
        self.assertEqual(data["user"]["team"]["display_name"], "Comercio de owner@example.com")


class AdminTeamProfilesTests(TeamApiTestCase):
    """GET /api/v1/teams/profiles/: los perfiles de cada comercio, de solo
    lectura, para la pestaña "Perfiles de los comercios" de roles.html."""

    URL = "/api/v1/teams/profiles/"

    def setUp(self):
        super().setUp()
        self.team.name = "Mi Comercio"
        self.team.save()
        self.admin = make_user("admin@example.com", group="admin")
        self.admin.is_staff = True
        self.admin.save()

    def get(self, user, query=""):
        return self.client.get(f"{self.URL}{query}", **bearer(user))

    def test_lists_each_business_with_its_profiles_and_members(self):
        add_employee(self.owner, "e@example.com", profile="Depósito")
        response = self.get(self.admin, "?search=mi comercio")
        self.assertEqual(response.status_code, 200, response.data)
        [business] = response.data["teams"]
        self.assertEqual(business["name"], "Mi Comercio")
        self.assertEqual(business["owner_email"], "owner@example.com")
        self.assertEqual(business["member_count"], 2)
        profiles = {p["name"]: p for p in business["profiles"]}
        self.assertEqual(set(profiles), {"Todo", "Depósito", "Atención"})
        self.assertEqual(profiles["Depósito"]["member_count"], 1)
        self.assertEqual(profiles["Depósito"]["accesses"], ["orders.view", "labels.print", "orders.dispatch"])

    def test_access_catalog_says_which_permissions_each_access_gates(self):
        accesses = {a["key"]: a for a in self.get(self.admin).data["accesses"]}
        self.assertEqual(set(accesses), set(teams.ACCESSES))
        self.assertEqual(accesses["labels.print"]["label"], "Imprimir rótulos")
        self.assertIn("labels.batch", accesses["labels.print"]["permissions"])
        self.assertIn("stores.manage", accesses["stores"]["permissions"])

    def test_search_by_owner_email(self):
        teams.team_for(make_user("otro@example.com"))
        response = self.get(self.admin, "?search=owner@")
        self.assertEqual([t["owner_email"] for t in response.data["teams"]], ["owner@example.com"])

    def test_merchant_cannot_read_other_businesses(self):
        self.assertEqual(self.get(self.owner).status_code, 403)


class AdminEditsTeamProfilesTests(TeamApiTestCase):
    """El admin de la plataforma edita los perfiles de cualquier comercio
    desde roles.html (Perfiles de los comercios)."""

    def setUp(self):
        super().setUp()
        self.admin = make_user("admin@example.com", group="admin")
        self.admin.is_staff = True
        self.admin.save()

    def call(self, method, url, body=None, user=None):
        return getattr(self.client, method)(url, body or {}, format="json", **bearer(user or self.admin))

    def test_admin_edits_creates_and_deletes_a_business_profile(self):
        url = f"/api/v1/teams/profiles/{self.deposito.id}/"
        response = self.call("patch", url, {"name": "Depósito 2", "accesses": ["orders.view", "documents.delete"]})
        self.assertEqual(response.status_code, 200, response.data)
        self.deposito.refresh_from_db()
        self.assertEqual(self.deposito.name, "Depósito 2")
        self.assertEqual(self.deposito.accesses, ["documents.delete", "orders.view"])
        self.assertTrue(AuditLog.objects.filter(action="team.profile_update", actor=self.admin).exists())

        created = self.call("post", f"/api/v1/teams/{self.team.id}/profiles/", {"name": "Ventas", "accesses": ["orders.view"]})
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(self.call("delete", f"/api/v1/teams/profiles/{created.data['id']}/").status_code, 204)

    def test_same_rules_as_the_business(self):
        add_employee(self.owner, "e@example.com", profile="Depósito")
        self.assertEqual(self.call("delete", f"/api/v1/teams/profiles/{self.deposito.id}/").status_code, 400)
        response = self.call("patch", f"/api/v1/teams/profiles/{self.deposito.id}/", {"name": "Todo"})
        self.assertEqual(response.status_code, 400)

    def test_a_merchant_cannot_edit_another_business(self):
        other = make_user("other@example.com")
        response = self.call("patch", f"/api/v1/teams/profiles/{self.deposito.id}/", {"name": "X"}, user=other)
        self.assertEqual(response.status_code, 403)
