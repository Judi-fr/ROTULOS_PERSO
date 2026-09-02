"""Rutas de autenticación (Google Sign-In, email/contraseña y refresh JWT)."""

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .dashboard_views import DashboardView
from .profile_views import ProfileView
from .support_views import SupportMessageView
from .role_permission_views import (
    PermissionCatalogView,
    RoleDetailView,
    RoleListView,
    RolePermissionsView,
)
from .views import (
    ChangePasswordView,
    EmailVerificationConfirmView,
    EmailVerificationResendView,
    GoogleAuthView,
    LoginView,
    LogoutView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RegisterView,
)

urlpatterns = [
    path("google/", GoogleAuthView.as_view(), name="google-auth"),
    path("login/", LoginView.as_view(), name="login"),
    path("register/", RegisterView.as_view(), name="register"),
    path("logout/", LogoutView.as_view(), name="logout"),
    # "Olvidé mi contraseña": paso 1 pide el link por email, paso 2 lo confirma
    # con el token del correo y la nueva contraseña. Ambos son públicos.
    path(
        "password-reset/",
        PasswordResetRequestView.as_view(),
        name="password-reset",
    ),
    path(
        "password-reset/confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    # Verificación suave de email (solo cuentas email/contraseña, ver
    # RegisterView): confirma el token del correo y permite reenviarlo.
    path(
        "verify-email/confirm/",
        EmailVerificationConfirmView.as_view(),
        name="verify-email-confirm",
    ),
    path(
        "verify-email/resend/",
        EmailVerificationResendView.as_view(),
        name="verify-email-resend",
    ),
    # Perfil del usuario autenticado: GET (ver) y PATCH (editar nombre/apellido).
    path("me/", ProfileView.as_view(), name="profile"),
    # Cambio de contraseña del usuario autenticado (exige la actual).
    path("me/change-password/", ChangePasswordView.as_view(), name="change-password"),
    # Form de contacto del dashboard ("Ayuda/Soporte"): solo guarda el mensaje.
    path("support/", SupportMessageView.as_view(), name="support-message"),
    # Menú del dashboard (no-admin): arma la lista de ítems según el rol
    # efectivo del usuario autenticado. Ver dashboard_views.py.
    path("users/me/dashboard/", DashboardView.as_view(), name="user-dashboard"),
    # Administración de roles y permisos (protegido por IsAdminUser).
    path("roles/", RoleListView.as_view(), name="role-list"),
    path("roles/<int:role_id>/", RoleDetailView.as_view(), name="role-detail"),
    path("roles/<int:role_id>/permissions/", RolePermissionsView.as_view(), name="role-permissions"),
    path("permissions/", PermissionCatalogView.as_view(), name="permission-catalog"),
    # Renueva el access token a partir de un refresh válido. Con la rotación
    # activada (ROTATE_REFRESH_TOKENS) devuelve también un refresh nuevo y
    # revoca el anterior.
    # Body: {"refresh": "<JWT>"}  ->  {"access": "<JWT nuevo>", "refresh": "..."}
    path("refresh/", TokenRefreshView.as_view(), name="token-refresh"),
]
