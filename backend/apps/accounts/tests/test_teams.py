"""Comercios con equipo: modelos, comercio de cada request y usuario de datos."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from apps.accounts import teams
from apps.accounts.models import Team, TeamMember

User = get_user_model()


def make_user(email, **extra):
    return User.objects.create_user(username=email, email=email, password="Clave123!", **extra)


class TeamForTests(TestCase):
    def test_nobody_gets_a_team_by_asking(self):
        user = make_user("solo@example.com")
        self.assertIsNone(teams.membership(user))
        with self.assertRaises(teams.NoTeam):
            teams.team_for(user)
        with self.assertRaises(teams.NoTeam):
            teams.data_owner(user)
        self.assertFalse(Team.objects.exists())

    def test_create_team_with_suggested_profiles_and_data_user(self):
        user = make_user("solo@example.com")
        team = teams.create_team(user, name="Solo")
        self.assertEqual(team.owner, user)
        self.assertEqual(teams.membership(user).role, TeamMember.Role.OWNER)
        self.assertEqual(teams.team_for(user), team)
        self.assertEqual(teams.data_owner(user), team.data_user)
        self.assertNotEqual(team.data_user, user)
        profiles = {profile.name: profile.accesses for profile in team.profiles.all()}
        self.assertEqual(set(profiles), {"Todo", "Depósito", "Atención"})
        self.assertEqual(profiles["Depósito"], ["orders.view", "labels.print", "orders.dispatch"])
        self.assertEqual(sorted(profiles["Todo"]), sorted(teams.ACCESSES))

    def test_data_owner_of_employee_is_the_team_data_user(self):
        owner = make_user("owner@example.com")
        employee = make_user("employee@example.com")
        team = teams.create_team(owner)
        TeamMember.objects.create(team=team, user=employee, role=TeamMember.Role.EMPLOYEE)
        self.assertEqual(teams.data_owner(employee), team.data_user)
        self.assertEqual(teams.data_owner(owner), team.data_user)

    def test_a_person_can_be_in_several_teams_once_each(self):
        owner = make_user("owner@example.com")
        employee = make_user("employee@example.com")
        own = teams.create_team(employee)
        team = teams.create_team(owner)
        TeamMember.objects.create(team=team, user=employee, role=TeamMember.Role.EMPLOYEE)
        self.assertEqual({m.team_id for m in teams.memberships(employee)}, {own.pk, team.pk})
        with self.assertRaises(IntegrityError):
            TeamMember.objects.create(team=team, user=employee, role=TeamMember.Role.EMPLOYEE)

    def test_one_owner_per_team(self):
        team = teams.create_team(make_user("owner@example.com"))
        with self.assertRaises(IntegrityError):
            TeamMember.objects.create(team=team, user=make_user("other@example.com"), role=TeamMember.Role.OWNER)

    def test_header_picks_the_team_only_if_it_is_hers(self):
        person = make_user("p@example.com")
        first = teams.create_team(person, name="Primero")
        second = teams.create_team(person, name="Segundo")
        stranger = teams.create_team(make_user("x@example.com"))
        with teams.request_cache(first.pk):
            self.assertEqual(teams.team_for(person), first)
        with teams.request_cache(stranger.pk):
            self.assertEqual(teams.team_for(person), second)  # el último usado
        with teams.request_cache():
            self.assertEqual(teams.team_for(person), second)


class TeamMigrationTests(TransactionTestCase):
    before = [("accounts", "0025_seed_monitoring_view_permission")]
    after = [("accounts", "0027_create_teams_for_users")]

    def test_migration_creates_a_team_for_every_existing_user(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        old_apps = executor.loader.project_state(self.before).apps
        OldUser = old_apps.get_model("auth", "User")
        OldUser.objects.create(username="a@example.com", email="a@example.com")
        OldUser.objects.create(username="b@example.com", email="b@example.com", is_staff=True)

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.after)
        new_apps = executor.loader.project_state(self.after).apps
        NewTeam = new_apps.get_model("accounts", "Team")
        NewMember = new_apps.get_model("accounts", "TeamMember")
        for email in ("a@example.com", "b@example.com"):
            team = NewTeam.objects.get(owner__email=email)
            self.assertEqual(NewMember.objects.get(user__email=email).role, "owner")
            self.assertEqual(team.profiles.count(), 3)

        # Dejar la base como la esperan los demás tests.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
