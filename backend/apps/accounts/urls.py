"""Rutas de autenticación (Google Sign-In, email/contraseña y refresh JWT)."""

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from .views import GoogleAuthView, LoginView, RegisterView

urlpatterns = [
    path("google/", GoogleAuthView.as_view(), name="google-auth"),
    path("login/", LoginView.as_view(), name="login"),
    path("register/", RegisterView.as_view(), name="register"),
    # Renueva el access token a partir de un refresh válido.
    # Body: {"refresh": "<JWT>"}  ->  {"access": "<JWT nuevo>"}
    path("refresh/", TokenRefreshView.as_view(), name="token-refresh"),
]
