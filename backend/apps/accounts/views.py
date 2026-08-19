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
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


def normalize_email(raw):
    """Normaliza el email para usarlo como identidad única del usuario.

    Todos los métodos de login deben pasar por acá: el email es el
    ``username`` del proyecto, así que ``Juan@Gmail.com`` y ``juan@gmail.com``
    tienen que resolver a la MISMA cuenta. Sin esto, Google (que devuelve el
    email tal cual) podría crear una cuenta duplicada frente al login manual.
    """
    return (raw or "").strip().lower()


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

    permission_classes = [AllowAny]

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

        # Con los scopes openid/email/profile el token trae estos claims.
        # Se normaliza igual que en login/registro para que Google resuelva a
        # la misma cuenta que el alta manual (ver normalize_email).
        email = normalize_email(claims.get("email"))
        if not email or not claims.get("email_verified", False):
            return Response(
                {"detail": "La cuenta de Google no tiene un email verificado."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        user, created = User.objects.get_or_create(
            username=email,
            defaults={
                "email": email,
                "first_name": claims.get("given_name", ""),
                "last_name": claims.get("family_name", ""),
            },
        )

        # Cuenta creada vía Google: no tiene contraseña propia. Se marca como
        # inutilizable explícitamente para que ningún login con password pueda
        # autenticarla (en vez de depender de que el campo quede en "").
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])

        # URL pública del avatar en el CDN de Google. No se persiste:
        # Google puede rotarla, así que se refresca en cada login.
        return auth_response(user, picture=claims.get("picture", ""))


class LoginView(APIView):
    """POST /api/v1/auth/login/

    Login con email + contraseña para usuarios que ya existen.

    Body:     {"email": "...", "password": "..."}
    Respuesta: {"access": "<JWT>", "refresh": "<JWT>", "user": {...}}
    """

    permission_classes = [AllowAny]
    # Rate limit dedicado: como máximo 5 intentos por minuto y por IP (rate
    # "login" definido en settings). Frena la fuerza bruta de contraseñas.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        email = normalize_email(request.data.get("email"))
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

    permission_classes = [AllowAny]

    def post(self, request):
        email = normalize_email(request.data.get("email"))
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

        # Fortaleza de contraseña. Dos capas:
        #  1) regla de composición del proyecto (mayúscula + número);
        #  2) validators de Django (AUTH_PASSWORD_VALIDATORS): longitud mínima,
        #     contraseñas comunes, solo-numéricas y similitud con el email.
        # La (2) no corría antes porque create_user() no invoca los validators.
        password_error = validate_password_strength(password)
        if password_error:
            return Response(
                {"detail": password_error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            validate_password(
                password,
                user=User(username=email, email=email,
                          first_name=first_name, last_name=last_name),
            )
        except ValidationError as exc:
            return Response(
                {"detail": " ".join(exc.messages)},
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


class LogoutView(APIView):
    """POST /api/v1/auth/logout/

    Cierra la sesión revocando el refresh token: lo manda a la blacklist para
    que no pueda volver a usarse en /refresh/ aunque todavía no haya expirado.
    El access token que ya esté en circulación sigue siendo válido hasta que
    expire (vida corta, 15 min) — es la naturaleza de los JWT stateless.

    Requiere estar autenticado (access válido en el header) y mandar el refresh
    a revocar en el body.

    Body:      {"refresh": "<JWT>"}
    Respuesta: 205 Reset Content
    """

    def post(self, request):
        refresh = request.data.get("refresh")
        if not refresh:
            return Response(
                {"detail": "Falta el campo 'refresh' (token a revocar)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            # blacklist() requiere la app token_blacklist y sus migraciones.
            RefreshToken(refresh).blacklist()
        except TokenError:
            # Token inválido, expirado o ya revocado: el objetivo (que no
            # sirva) ya está cumplido, así que se responde igual como éxito.
            pass
        return Response(status=status.HTTP_205_RESET_CONTENT)


def blacklist_all_refresh_tokens(user):
    """Revoca TODOS los refresh tokens vigentes del usuario.

    Se usa al resetear la contraseña: si la cuenta pudo haber sido
    comprometida, cambiar la contraseña no debe dejar sesiones abiertas con
    los refresh viejos. Recorre los OutstandingToken (los que emitió
    SimpleJWT) y los manda a la blacklist; los que ya estaban revocados se
    ignoran gracias a get_or_create.
    """
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)


class PasswordResetRequestView(APIView):
    """POST /api/v1/auth/password-reset/

    Paso 1 del "olvidé mi contraseña": el usuario manda su email y, si existe
    una cuenta, se le envía un correo con un link al frontend que lleva el
    ``uid`` y un ``token`` firmado.

    El token lo genera ``default_token_generator`` de Django: no se persiste
    en la base porque se deriva del hash de la contraseña actual + ``last_login``
    + un timestamp. Consecuencias: caduca solo (``PASSWORD_RESET_TIMEOUT``) y se
    invalida en cuanto la contraseña cambia, así que un link ya usado no sirve.

    Body:      {"email": "..."}
    Respuesta: 200 SIEMPRE, exista o no la cuenta (evita enumerar usuarios).
    """

    permission_classes = [AllowAny]
    # Frena el envío masivo de correos (email bombing) a una víctima.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    # Respuesta única: no revela si el email está registrado o no.
    GENERIC_MESSAGE = (
        "Si existe una cuenta con ese email, te enviamos un enlace para "
        "restablecer la contraseña."
    )

    def post(self, request):
        email = normalize_email(request.data.get("email"))
        if not email:
            return Response(
                {"detail": "Falta el campo 'email'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # No se corta si el formato es inválido ni si no existe la cuenta: en
        # ambos casos se responde igual para no filtrar qué emails existen.
        user = User.objects.filter(username__iexact=email, is_active=True).first()
        if user is not None:
            self._send_reset_email(user)

        return Response({"detail": self.GENERIC_MESSAGE})

    def _send_reset_email(self, user):
        """Arma el link al frontend y envía el correo de reset."""
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        # El link apunta al FRONTEND (la SPA o, en pruebas, la página de
        # oauth-test), no a una vista de Django: esa página toma uid+token de la
        # URL y hace el POST a /password-reset/confirm/. La base es configurable
        # (PASSWORD_RESET_URL) para no atar el correo a un puerto/ruta fijos.
        reset_url = f"{settings.PASSWORD_RESET_URL}?uid={uid}&token={token}"

        send_mail(
            subject="Restablecé tu contraseña — ROTULOS",
            message=(
                f"Hola,\n\n"
                f"Recibimos un pedido para restablecer la contraseña de tu "
                f"cuenta. Para elegir una nueva, entrá en este enlace:\n\n"
                f"{reset_url}\n\n"
                f"Si no fuiste vos, ignorá este correo: tu contraseña actual "
                f"sigue siendo válida.\n"
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )


class PasswordResetConfirmView(APIView):
    """POST /api/v1/auth/password-reset/confirm/

    Paso 2 del "olvidé mi contraseña": el frontend manda el ``uid`` y el
    ``token`` que venían en el link, más la ``new_password`` elegida.

    Valida el token, aplica las mismas reglas de contraseña que el registro
    (composición propia + validators de Django) y, al setear la nueva, revoca
    todos los refresh tokens vigentes del usuario (ver
    ``blacklist_all_refresh_tokens``).

    Body:      {"uid": "...", "token": "...", "new_password": "..."}
    Respuesta: 200 con un mensaje de éxito.
    """

    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "password_reset"

    def post(self, request):
        uid = request.data.get("uid") or ""
        token = request.data.get("token") or ""
        new_password = request.data.get("new_password") or ""

        if not uid or not token or not new_password:
            return Response(
                {"detail": "Faltan los campos 'uid', 'token' y 'new_password'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # El uid viaja codificado en base64 (urlsafe). Si no decodifica a un
        # usuario válido, se responde con el mismo error genérico que un token
        # inválido para no dar pistas.
        user = self._get_user(uid)
        if user is None or not default_token_generator.check_token(user, token):
            return Response(
                {"detail": "El enlace de restablecimiento no es válido o expiró."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Mismas dos capas de validación que el registro (ver RegisterView):
        # composición propia + validators de Django (con el usuario para la
        # similitud con el email).
        password_error = validate_password_strength(new_password)
        if password_error:
            return Response(
                {"detail": password_error},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            validate_password(new_password, user=user)
        except ValidationError as exc:
            return Response(
                {"detail": " ".join(exc.messages)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])

        # Cambiar la contraseña ya invalida futuros tokens de reset (el
        # generator depende del hash), pero NO cierra las sesiones activas: se
        # revocan los refresh vigentes por si la cuenta estaba comprometida.
        blacklist_all_refresh_tokens(user)

        return Response(
            {"detail": "Tu contraseña se actualizó. Ya podés iniciar sesión."}
        )

    def _get_user(self, uidb64):
        """Decodifica el uid base64 y devuelve el usuario, o None si falla."""
        try:
            pk = force_str(urlsafe_base64_decode(uidb64))
            return User.objects.get(pk=pk)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return None
