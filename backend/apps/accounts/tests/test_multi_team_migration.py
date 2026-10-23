"""La migración a varios comercios por persona (``accounts/0034``): los
comercios vacíos se borran, los que tienen datos o empleados pasan sus datos
a un usuario de datos nuevo, y los dados de baja usan su usuario archivo."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


def _targets(accounts_migration):
    """Todas las apps en su última migración, salvo ``accounts`` en la dada
    (así el estado histórico incluye pedidos, avisos, etc.)."""
    executor = MigrationExecutor(connection)
    leaves = [node for node in executor.loader.graph.leaf_nodes() if node[0] != "accounts"]
    return [*leaves, ("accounts", accounts_migration)]


class MultiTeamMigrationTests(TransactionTestCase):
    def setUp(self):
        self.before = _targets("0033_multi_team_schema")
        self.after = _targets("0035_multi_team_finish")

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        # Dejar la base como la esperan los demás tests.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_empty_teams_go_and_data_moves_to_a_data_user(self):
        old = self.migrate(self.before)
        User = old.get_model("auth", "User")
        Team = old.get_model("accounts", "Team")
        Member = old.get_model("accounts", "TeamMember")
        Address = old.get_model("orders", "Address")
        Notice = old.get_model("monitoring", "MerchantNotice")

        def team_for(email):
            person = User.objects.create(username=email, email=email)
            team = Team.objects.create(owner=person)
            Member.objects.create(team=team, user=person, role="owner")
            return person, team

        with_data, team_data = team_for("datos@example.com")
        Address.objects.create(user=with_data, street="Calle", number="1", city="X", state="Y", postal_code="1000")
        Notice.objects.create(owner=with_data, key="k", kind="store", title="Aviso", access="stores")
        _, team_empty = team_for("vacio@example.com")
        boss, team_staff = team_for("jefa@example.com")
        employee = User.objects.create(username="emple@example.com", email="emple@example.com")
        Member.objects.create(team=team_staff, user=employee, role="employee")
        closed_person = User.objects.create(username="baja@example.com", email="baja@example.com")
        archive = User.objects.create(username="baja-comercio@archivado.invalid", email="baja-comercio@archivado.invalid")
        team_closed = Team.objects.create(owner=archive, former_owner=closed_person, closed_at=timezone.now())
        Member.objects.create(team=team_closed, user=archive, role="owner")
        Address.objects.create(user=archive, street="Vieja", number="2", city="X", state="Y", postal_code="1000")

        new = self.migrate(self.after)
        Team = new.get_model("accounts", "Team")
        Member = new.get_model("accounts", "TeamMember")
        Address = new.get_model("orders", "Address")
        Notice = new.get_model("monitoring", "MerchantNotice")

        self.assertFalse(Team.objects.filter(pk=team_empty.pk).exists())

        moved = Team.objects.get(pk=team_data.pk)
        self.assertEqual(moved.owner_id, with_data.pk)
        self.assertNotEqual(moved.data_user_id, with_data.pk)
        self.assertTrue(moved.data_user.email.endswith("@datos.invalid"))
        self.assertFalse(moved.data_user.is_active)
        self.assertEqual(Address.objects.get(street="Calle").user_id, moved.data_user_id)
        self.assertEqual(Notice.objects.get().owner_id, moved.data_user_id)
        self.assertTrue(moved.join_code)

        staff = Team.objects.get(pk=team_staff.pk)
        self.assertEqual(staff.owner_id, boss.pk)
        self.assertEqual(staff.members.count(), 2)

        closed = Team.objects.get(pk=team_closed.pk)
        self.assertEqual((closed.owner_id, closed.data_user_id), (closed_person.pk, archive.pk))
        self.assertFalse(Member.objects.filter(team=closed).exists())
        self.assertFalse(closed.join_code_enabled)
        self.assertEqual(Address.objects.get(street="Vieja").user_id, archive.pk)
