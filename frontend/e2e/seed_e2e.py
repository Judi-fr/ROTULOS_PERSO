# Cuentas de las pruebas de navegador, además de las de seed_demo. Solo en su base propia.
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from apps.accounts.team_models import Team
from apps.accounts.tests.test_team_permissions import add_employee

User = get_user_model()
PASSWORD = "E2eRotulos2026"

admin = User.objects.create_user("e2e-admin@rotulos.local", "e2e-admin@rotulos.local", PASSWORD, is_staff=True)
admin.groups.add(Group.objects.get(name="admin"))

owner = User.objects.get(username="demo@rotulos.local")
employee, _ = add_employee(owner, "e2e-empleado@rotulos.local", profile="Depósito")
employee.set_password(PASSWORD)
employee.save()
print(Team.objects.count())
