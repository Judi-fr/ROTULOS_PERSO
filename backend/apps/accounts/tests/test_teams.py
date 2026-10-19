"""Comercios con equipo: modelos y comercio propio de cada usuario."""

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
    def test_team_for_creates_solo_team_with_suggested_profiles(self):
        user = make_user("solo@example.com")
        team = teams.team_for(user)
        self.assertEqual(team.owner, user)
        self.assertEqual(teams.membership(user).role, TeamMember.Role.OWNER)
        profiles = {profile.name: profile.accesses for profile in team.profiles.all()}
        self.assertEqual(set(profiles), {"Todo", "Depósito", "Atención"})
        self.assertEqual(profiles["Depósito"], ["orders.view", "labels.print", "orders.dispatch"])
        self.assertEqual(sorted(profiles["Todo"]), sorted(teams.ACCESSES))
        # Llamarlo de nuevo no crea otro.
        self.assertEqual(teams.team_for(user), team)
        self.assertEqual(Team.objects.count(), 1)

    def test_data_owner_of_employee_is_team_owner(self):
        owner = make_user("owner@example.com")
        employee = make_user("employee@example.com")
        team = teams.team_for(owner)
        TeamMember.objects.create(team=team, user=employee, role=TeamMember.Role.EMPLOYEE)
        self.assertEqual(teams.data_owner(employee), owner)
        self.assertEqual(teams.data_owner(owner), owner)

    def test_user_belongs_to_one_team_only(self):
        owner = make_user("owner@example.com")
        employee = make_user("employee@example.com")
        teams.team_for(employee)
        with self.assertRaises(IntegrityError):
            TeamMember.objects.create(team=teams.team_for(owner), user=employee, role=TeamMember.Role.EMPLOYEE)


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
