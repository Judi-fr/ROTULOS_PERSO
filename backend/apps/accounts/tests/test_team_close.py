"""Baja del comercio (soft delete: los datos quedan en su usuario de datos y
nadie sigue siendo miembro) y reapertura por el admin."""

from rest_framework.test import APITestCase

from apps.accounts import teams
from apps.accounts.models import Team, TeamInvitation, TeamMember
from apps.audit.models import AuditLog
from apps.carriers.models import CarrierAccount
from apps.integrations.models import StoreConnection
from apps.labels.models import LabelTemplate
from apps.orders.models import Address, Order
from apps.printing.models import PrintAgent

from .test_team_permissions import add_employee, bearer, make_owner, make_user

CLOSE = "/api/v1/team/close/"


class CloseTeamTestCase(APITestCase):
    def setUp(self):
        self.owner = make_owner("owner@example.com", name="Mi Tienda")
        self.team = teams.team_for(self.owner)
        self.data = self.team.data_user
        self.store = StoreConnection.objects.create(
            owner=self.data, platform=StoreConnection.Platform.TIENDANUBE, external_store_id="77", name="T1",
        )
        self.store.access_token = "secreto"
        self.store.save()
        address = Address.objects.create(user=self.data, street="C", number="1", city="CABA")
        self.order = Order.objects.create(user=self.data, address=address, store_connection=self.store, external_id="1")
        self.template = LabelTemplate.objects.create(owner=self.data, name="P", width_cm=10, height_cm=15, design={})
        self.carrier = CarrierAccount.objects.create(owner=self.data, carrier="andreani")
        self.agent = PrintAgent.objects.create(owner=self.data, name="PC", token_prefix="abc", token_hash="x" * 64)

    def close(self, user=None, **body):
        payload = {"confirm": "BAJA", "reason": "Cerramos el local", **body}
        return self.client.post(CLOSE, payload, format="json", **bearer(user or self.owner))


class CloseTeamTests(CloseTeamTestCase):
    def test_close_hides_everything_but_keeps_it_in_the_database(self):
        employee, _ = add_employee(self.owner, "e@example.com", profile="Todo")
        response = self.close()
        self.assertEqual(response.status_code, 200, response.data)

        team = Team.objects.get(pk=self.team.pk)
        self.assertTrue(team.is_closed)
        self.assertEqual((team.owner, team.closed_by, team.close_reason), (self.owner, self.owner, "Cerramos el local"))
        # Los datos siguen ahí, en el usuario de datos del comercio.
        for obj, field in ((self.order, "user"), (self.template, "owner"), (self.store, "owner")):
            obj.refresh_from_db()
            self.assertEqual(getattr(obj, field), self.data)
        # Nadie sigue siendo miembro: dueño y empleado quedan sin comercio
        # (no se les crea uno) y no ven nada.
        self.assertFalse(team.members.exists())
        for person in (self.owner, employee):
            self.assertIsNone(teams.membership(person))
        self.assertEqual(self.client.get("/api/v1/orders/", **bearer(self.owner)).data["code"], "no_team")
        self.assertIsNone(response.data["user"]["team"])
        # Lo que trabaja solo, apagado.
        self.store.refresh_from_db()
        self.assertEqual(self.store.status, StoreConnection.Status.REVOKED)
        self.assertEqual(self.store.access_token, "")
        self.assertEqual(self.store.external_store_id, f"77#baja-{team.pk}")
        self.carrier.refresh_from_db()
        self.agent.refresh_from_db()
        self.assertFalse(self.carrier.is_active)
        self.assertFalse(self.agent.is_active)
        self.assertTrue(AuditLog.objects.filter(action="team.close", target_id=str(team.pk)).exists())

    def test_only_the_owner_and_with_the_confirmation_word(self):
        employee, _ = add_employee(self.owner, "e@example.com", profile="Todo")
        self.assertEqual(self.close(user=employee).status_code, 403)
        self.assertEqual(self.close(confirm="si").status_code, 400)
        self.assertFalse(Team.objects.get(pk=self.team.pk).is_closed)

    def test_pending_invitations_are_cancelled(self):
        self.client.post(
            "/api/v1/team/invitations/", {"email": "x@example.com"}, format="json", **bearer(self.owner)
        )
        self.close()
        self.assertEqual(TeamInvitation.objects.get().status, TeamInvitation.Status.CANCELLED)

    def test_the_same_store_can_be_connected_again_as_a_new_one(self):
        self.close()
        fresh = StoreConnection.objects.create(
            owner=teams.create_team(self.owner).data_user,
            platform=StoreConnection.Platform.TIENDANUBE,
            external_store_id="77",
            name="T1",
        )
        self.assertNotEqual(fresh.pk, self.store.pk)


class ReopenTeamTests(CloseTeamTestCase):
    def setUp(self):
        super().setUp()
        self.admin = make_user("admin@example.com", group="admin")
        self.admin.is_staff = True
        self.admin.save()

    def test_admin_lists_and_reopens(self):
        self.close()
        listed = self.client.get("/api/v1/teams/closed/", **bearer(self.admin))
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual([row["owner_email"] for row in listed.data], ["owner@example.com"])
        self.assertEqual(listed.data[0]["data"]["pedido(s)"], 1)
        self.assertNotIn(self.team.pk, [t["id"] for t in self.client.get("/api/v1/teams/", **bearer(self.admin)).data])
        users = self.client.get("/api/v1/users/?page_size=100", **bearer(self.admin)).data["results"]
        self.assertNotIn(self.data.email, [u["email"] for u in users])

        response = self.client.post(f"/api/v1/teams/{self.team.pk}/reopen/", **bearer(self.admin))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["warnings"], [])
        team = Team.objects.get(pk=self.team.pk)
        self.assertFalse(team.is_closed)
        self.assertEqual(team.owner, self.owner)
        self.assertEqual(teams.team_for(self.owner), team)
        self.order.refresh_from_db()
        self.store.refresh_from_db()
        self.carrier.refresh_from_db()
        self.assertEqual(self.order.user, self.data)
        self.assertEqual(self.store.external_store_id, "77")
        self.assertTrue(self.carrier.is_active)
        self.assertTrue(AuditLog.objects.filter(action="team.reopen").exists())

    def test_reopen_works_even_if_the_owner_has_another_business_with_data(self):
        self.close()
        other = teams.create_team(self.owner, name="Otro")
        Address.objects.create(user=other.data_user, street="Otra", number="2", city="CABA")
        response = self.client.post(f"/api/v1/teams/{self.team.pk}/reopen/", **bearer(self.admin))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({m.team_id for m in teams.memberships(self.owner)}, {self.team.pk, other.pk})

    def test_store_reconnected_meanwhile_stays_archived(self):
        self.close()
        StoreConnection.objects.create(
            owner=make_user("otro@example.com"), platform=StoreConnection.Platform.TIENDANUBE, external_store_id="77",
        )
        response = self.client.post(f"/api/v1/teams/{self.team.pk}/reopen/", **bearer(self.admin))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["warnings"]), 1)
        self.store.refresh_from_db()
        self.assertEqual(self.store.owner, self.data)
        self.assertTrue(self.store.external_store_id.startswith("77#baja-"))

    def test_merchant_cannot_reopen(self):
        self.close()
        teams.create_team(self.owner)
        response = self.client.post(f"/api/v1/teams/{self.team.pk}/reopen/", **bearer(self.owner))
        self.assertEqual(response.status_code, 403)
