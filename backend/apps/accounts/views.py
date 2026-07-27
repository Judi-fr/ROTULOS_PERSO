"""Autenticación.

Todos los métodos de login (Google Sign-In y email/contraseña) emiten el
mismo par de tokens JWT y devuelven el mismo objeto ``user``, de modo que
el frontend maneja una única estructura de respuesta sin importar cómo se
haya autenticado el usuario:

    {"access": "<JWT>", "refresh": "<JWT>", "user": {...}}
"""

import re

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


def tokens_for_user(user):
    """Genera el par de tokens JWT (access + refresh) para un usuario.

    Formato de respuesta compartido por todos los métodos de login
    (Google, email/contraseña, registro) para que el frontend maneje
    siempre la misma estructura sin importar cómo se autenticó.
    """
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


def user_payload(user, picture=""):
    """Objeto ``user`` que acompaña a los tokens en cada respuesta de login.

    ``picture`` solo lo aporta Google (viene en el ID token y no se
    persiste); en el login con email/contraseña queda vacío.
    """
    return {
        "id": user.id,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "picture": picture,
    }


def auth_response(user, picture=""):
    """Respuesta estándar de login: tokens JWT + objeto user."""
    return Response({**tokens_for_user(user), "user": user_payload(user, picture)})


# Formato de email: algo@algo.dominio (sin espacios ni un segundo @).
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_email_format(email):
    """Devuelve un mensaje de error si el email no tiene formato válido, o None."""
    if not EMAIL_REGEX.match(email):
        return "El email no tiene un formato válido."
    return None


def validate_password_strength(password):
    """Reglas de contraseña: mínimo 6 caracteres, una mayúscula y un número.

    Devuelve un mensaje de error (enumerando lo que falta), o None si es válida.
    """
    faltantes = []
    if len(password) < 6:
        faltantes.append("al menos 6 caracteres")
    if not re.search(r"[A-Z]", password):
        faltantes.append("una letra mayúscula")
    if not re.search(r"\d", password):
        faltantes.append("un número")
    if faltantes:
        return "La contraseña debe tener " + ", ".join(faltantes) + "."
    return None


class GoogleAuthView(APIView):
    """POST /api/v1/auth/google/

    Body:     {"credential": "<ID token de Google>"}
    Respuesta: {"access": "<JWT>", "refresh": "<JWT>", "user": {...}}
    """

    def post(self, request):
        credential = request.data.get("credential")
        if not credential:
            return Response(
                {"detail": "Falta el campo 'credential' (ID token de Google)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not settings.GOOGLE_CLIENT_ID:
            return Response(
                {"detail": "GOOGLE_CLIENT_ID no está configurado en el servidor."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        try:
            # Verifica firma, expiración y que el audience sea nuestro client_id
            claims = id_token.verify_oauth2_token(
                credential,
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID,
            )
        except ValueError:
            return Response(
                {"detail": "El ID token de Google no es válido o expiró."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Con los scopes openid/email/profile el token trae estos claims
        email = claims.get("email")
        if not email or not claims.get("email_verified", False):
            return Response(
                {"detail": "La cuenta de Google no tiene un email verificado."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        user, _created = User.objects.get_or_create(
            username=email,
            defaults={
                "email": email,
                "first_name": claims.get("given_name", ""),
                "last_name": claims.get("family_name", ""),
            },
        )

        # URL pública del avatar en el CDN de Google. No se persiste:
        # Google puede rotarla, así que se refresca en cada login.
        return auth_response(user, picture=claims.get("picture", ""))


class LoginView(APIView):
    """POST /api/v1/auth/login/

    Login con email + contraseña para usuarios que ya existen.

    Body:     {"email": "...", "password": "..."}
    Respuesta: {"access": "<JWT>", "refresh": "<JWT>", "user": {...}}
    """

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""

        if not email or not password:
            return Response(
                {"detail": "Faltan los campos 'email' y 'password'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email_error = validate_email_format(email)
        if email_error:
            return Response(
                {"detail": email_error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # El proyecto usa el email como username (ver GoogleAuthView).
        # authenticate() valida la contraseña y respeta is_active.
        user = authenticate(request, username=email, password=password)
        if user is None:
            return Response(
                {"detail": "Email o contraseña incorrectos."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return auth_response(user)


class RegisterView(APIView):
    """POST /api/v1/auth/register/

    Crea una cuenta con email + contraseña y devuelve al usuario ya logueado
    (mismos tokens que el resto de los métodos).

    Body:     {"email": "...", "password": "...",
               "first_name": "(opcional)", "last_name": "(opcional)"}
    Respuesta: {"access": "<JWT>", "refresh": "<JWT>", "user": {...}}  (201)
    """

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""
        first_name = (request.data.get("first_name") or "").strip()
        last_name = (request.data.get("last_name") or "").strip()

        if not email or not password:
            return Response(
                {"detail": "Faltan los campos 'email' y 'password'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Formato de email (regex).
        email_error = validate_email_format(email)
        if email_error:
            return Response(
                {"detail": email_error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Fortaleza de contraseña: mínimo 6 caracteres, una mayúscula, un número.
        password_error = validate_password_strength(password)
        if password_error:
            return Response(
                {"detail": password_error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # El email es el username: si ya existe, no se puede registrar de nuevo.
        if User.objects.filter(username__iexact=email).exists():
            return Response(
                {"detail": "Ya existe una cuenta con ese email."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # create_user hashea la contraseña automáticamente.
        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )

        return Response(
            {
                "detail": "Cuenta creada con éxito.",
                **tokens_for_user(user),
                "user": user_payload(user),
            },
            status=status.HTTP_201_CREATED,
        )
