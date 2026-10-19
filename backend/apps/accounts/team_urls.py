"""``/api/v1/team/``: el equipo del comercio (ver ``team_views.py``)."""

from django.urls import path

from . import team_views as views

urlpatterns = [
    path("", views.TeamView.as_view(), name="team"),
    path("members/<int:pk>/", views.MemberView.as_view(), name="team-member"),
    path("invitations/", views.InvitationListView.as_view(), name="team-invitations"),
    path("invitations/<int:pk>/", views.InvitationView.as_view(), name="team-invitation"),
    path("invitations/<int:pk>/resend/", views.InvitationResendView.as_view(), name="team-invitation-resend"),
    path("profiles/", views.ProfileListView.as_view(), name="team-profiles"),
    path("profiles/<int:pk>/", views.ProfileView.as_view(), name="team-profile"),
    path("transfer/", views.TransferView.as_view(), name="team-transfer"),
    path("activity/", views.TeamActivityView.as_view(), name="team-activity"),
    path("my-invitations/", views.MyInvitationListView.as_view(), name="team-my-invitations"),
    path(
        "my-invitations/<int:pk>/<str:decision>/",
        views.MyInvitationActionView.as_view(),
        name="team-my-invitation-action",
    ),
]
