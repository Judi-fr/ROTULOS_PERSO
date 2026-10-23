"""Permisos = rol global (techo de la plataforma) ∩ accesos del equipo."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts import teams
from apps.accounts.auth_views import user_payload
from apps.accounts.models import TeamMember
from apps.accounts.permissions_map import TEAM_SCOPED_PERMISSIONS, user_has_permission

User = get_user_model()


def bearer(user):
    return {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(user).access_token}"}


def make_user(email, group="subscriber"):
    user = User.objects.create_user(username=email, email=email, password="Clave123!")
    user.groups.add(Group.objects.get_or_create(name=group)[0])
    return user


def make_owner(email, group="subscriber", name=""):
    """Una persona con su comercio (con su propio usuario de datos)."""
    user = make_user(email, group)
    teams.create_team(user, name=name)
    return user


def add_employee(owner, email, *, profile=None, accesses=()):
    """Suma un empleado (que no está en ningún otro comercio) al comercio
    actual de ``owner`` (si ``owner`` todavía no tiene comercio, el
    transitorio "solo" de ``team_helpers``)."""
    from .team_helpers import give_solo_team

    employee = make_user(email)
    member = teams.membership(owner)
    team = member.team if member is not None else give_solo_team(owner)
    member = TeamMember.objects.create(
        team=team,
        user=employee,
        role=TeamMember.Role.EMPLOYEE,
        profile=team.profiles.get(name=profile) if profile else None,
        accesses=list(accesses),
    )
    return employee, member


class TeamPermissionTests(APITestCase):
    def setUp(self):
        self.owner = make_owner("owner@example.com")

    def test_owner_has_every_team_permission(self):
        missing = [key for key in TEAM_SCOPED_PERMISSIONS if not user_has_permission(self.owner, key)]
        self.assertEqual(missing, [])

    def test_employee_only_gets_profile_accesses(self):
        employee, _ = add_employee(self.owner, "dep@example.com", profile="Depósito")
        self.assertTrue(user_has_permission(employee, "labels.batch"))
        self.assertTrue(user_has_permission(employee, "orders.dispatch"))
        self.assertTrue(user_has_permission(employee, "orders.view"))
        for key in ("stores.manage", "orders.cancel", "team.manage", "plantillas.edit", "orders.import"):
            self.assertFalse(user_has_permission(employee, key), key)

    def test_profile_change_applies_to_its_members(self):
        employee, member = add_employee(self.owner, "dep@example.com", profile="Depósito")
        self.assertFalse(user_has_permission(employee, "orders.cancel"))
        profile = member.profile
        profile.accesses = profile.accesses + ["orders.cancel"]
        profile.save()
        self.assertTrue(user_has_permission(employee, "orders.cancel"))

    def test_custom_accesses_when_no_profile(self):
        employee, _ = add_employee(self.owner, "custom@example.com", accesses=["orders.view"])
        self.assertTrue(user_has_permission(employee, "orders.view"))
        self.assertFalse(user_has_permission(employee, "labels.batch"))

    def test_personal_permissions_do_not_depend_on_the_team(self):
        employee, _ = add_employee(self.owner, "nada@example.com", accesses=[])
        self.assertTrue(user_has_permission(employee, "users.me.view"))
        self.assertTrue(user_has_permission(employee, "support.create"))

    def test_role_is_still_the_ceiling(self):
        limited = make_user("limited@example.com", group="sin-permisos-de-pedidos")
        self.assertFalse(user_has_permission(limited, "orders.view"))

    def test_payload_includes_team(self):
        employee, _ = add_employee(self.owner, "dep@example.com", profile="Depósito")
        team = user_payload(employee)["team"]
        self.assertEqual(team["role"], "employee")
        self.assertEqual(team["accesses"], sorted(["orders.view", "labels.print", "orders.dispatch"]))
        self.assertTrue(team["all_stores"])
        self.assertEqual(user_payload(self.owner)["team"]["role"], "owner")

    def test_menu_shows_team_items_only_with_team_access(self):
        keys = {item["key"] for item in self.client.get("/api/v1/auth/users/me/dashboard/", **bearer(self.owner)).data["menu"]}
        self.assertTrue({"team", "team_activity"} <= keys)
        employee, _ = add_employee(self.owner, "dep@example.com", profile="Depósito")
        keys = {item["key"] for item in self.client.get("/api/v1/auth/users/me/dashboard/", **bearer(employee)).data["menu"]}
        self.assertFalse({"team", "team_activity"} & keys)
