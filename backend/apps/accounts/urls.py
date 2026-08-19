"""Rutas de autenticación (Google Sign-In, email/contraseña y refresh JWT)."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import GoogleAuthView, LoginView, LogoutView, MeView, RegisterView, VerifyEmailView
from .viewsets import UserAdminViewSet

router = DefaultRouter()
router.register(r"users", UserAdminViewSet, basename="user-admin")

urlpatterns = [
    path("google/", GoogleAuthView.as_view(), name="google-auth"),
    path("login/", LoginView.as_view(), name="login"),
    path("register/", RegisterView.as_view(), name="register"),
    path("verify-email/", VerifyEmailView.as_view(), name="verify-email"),
    # Renueva el access token a partir de un refresh válido.
    # Body: {"refresh": "<JWT>"}  ->  {"access": "<JWT nuevo>"}
    path("refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", MeView.as_view(), name="me"),
    path("", include(router.urls)),
]

