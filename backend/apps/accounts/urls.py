"""Rutas de autenticación (Google Sign-In, email/contraseña y refresh JWT)."""

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .profile_views import ProfileView
from .views import (
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
    # Perfil del usuario autenticado: GET (ver) y PATCH (editar nombre/apellido).
    path("me/", ProfileView.as_view(), name="profile"),
    # Renueva el access token a partir de un refresh válido. Con la rotación
    # activada (ROTATE_REFRESH_TOKENS) devuelve también un refresh nuevo y
    # revoca el anterior.
    # Body: {"refresh": "<JWT>"}  ->  {"access": "<JWT nuevo>", "refresh": "..."}
    path("refresh/", TokenRefreshView.as_view(), name="token-refresh"),
]
