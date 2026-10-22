"""``GET /api/v1/team/activity/mine/``: lo que hizo cada uno, para cualquier
miembro (sin el acceso "Equipo")."""

from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.audit.services import record

from .test_team_permissions import add_employee, bearer, make_user

URL = "/api/v1/team/activity/mine/"


class MyActivityTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com")
        self.employee, self.member = add_employee(self.owner, "dep@example.com", profile="Depósito")

    def actions(self, user, **params):
        response = self.client.get(URL, params, **bearer(user))
        self.assertEqual(response.status_code, 200, response.data)
        return [row["action"] for row in response.data["results"]]

    def test_an_employee_without_team_access_sees_only_their_own(self):
        record(actor=self.employee, category="orders", action="order.ship", target_repr="Pedido 1")
        record(actor=self.owner, category="orders", action="order.cancel", target_repr="Pedido 2")
        self.assertEqual(self.client.get("/api/v1/team/activity/", **bearer(self.employee)).status_code, 403)
        self.assertEqual(self.actions(self.employee), ["order.ship"])
        self.assertEqual(self.actions(self.owner), ["order.cancel"])

    def test_actor_param_cannot_show_someone_else(self):
        record(actor=self.owner, category="orders", action="order.cancel", target_repr="Pedido 2")
        self.assertEqual(self.actions(self.employee, actor=self.owner.pk), [])

    def test_platform_actions_and_history_before_joining_stay_out(self):
        record(actor=self.employee, category="auth", action="auth.login_success")
        record(actor=self.employee, category="orders", action="order.ship", target_repr="De antes")
        AuditLog.objects.filter(target_repr="De antes").update(created_at=self.member.joined_at - timedelta(days=1))
        record(actor=self.employee, category="orders", action="order.ship", target_repr="Pedido 3")
        response = self.client.get(URL, **bearer(self.employee))
        self.assertEqual([row["target_repr"] for row in response.data["results"]], ["Pedido 3"])

    def test_filters_and_no_ip_in_the_answer(self):
        record(actor=self.employee, category="orders", action="order.ship", target_repr="Pedido 10")
        record(actor=self.employee, category="labels", action="label.batch", target_repr="Lote")
        response = self.client.get(URL, {"search": "Pedido 10"}, **bearer(self.employee))
        self.assertEqual([row["target_repr"] for row in response.data["results"]], ["Pedido 10"])
        self.assertNotIn("ip_address", response.data["results"][0])
        today = timezone.localdate().isoformat()
        self.assertEqual(len(self.actions(self.employee, date_from=today, date_to=today)), 2)

    def test_needs_a_session(self):
        self.assertEqual(self.client.get(URL).status_code, 401)
