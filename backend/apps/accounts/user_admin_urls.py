"""Rutas de la administración de usuarios (CRUD del admin).

Se montan bajo ``/api/v1/users/`` (ver config/urls.py). Se usa SimpleRouter
(y no DefaultRouter) porque la API es JSON pura y no necesita la vista raíz
navegable que agrega DefaultRouter.
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from .team_views import (
    AdminClosedTeamListView,
    AdminTeamListView,
    AdminTeamProfileCreateView,
    AdminTeamProfilesView,
    AdminTeamProfileView,
    AdminTeamReopenView,
    AdminUserTeamView,
)
from .user_admin_views import UserAdminViewSet

router = SimpleRouter()
router.register(r"users", UserAdminViewSet, basename="user")

urlpatterns = [
    # El comercio de un usuario (comercios con equipo, apps/accounts/teams.py).
    path("users/<int:pk>/team/", AdminUserTeamView.as_view(), name="user-team"),
    path("teams/", AdminTeamListView.as_view(), name="admin-teams"),
    path("teams/closed/", AdminClosedTeamListView.as_view(), name="admin-teams-closed"),
    path("teams/<int:pk>/reopen/", AdminTeamReopenView.as_view(), name="admin-team-reopen"),
    path("teams/profiles/", AdminTeamProfilesView.as_view(), name="admin-team-profiles"),
    path("teams/profiles/<int:pk>/", AdminTeamProfileView.as_view(), name="admin-team-profile"),
    path("teams/<int:team_id>/profiles/", AdminTeamProfileCreateView.as_view(), name="admin-team-profile-create"),
] + router.urls
