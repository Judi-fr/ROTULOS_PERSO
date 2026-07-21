"""Autenticación con Google (Sign-In).

Flujo A (ID token): el frontend obtiene un ID token de Google Identity
Services en el navegador y lo envía a este endpoint. Acá se verifica la
firma del token contra el GOOGLE_CLIENT_ID y, si es válido, se crea o
recupera el usuario de Django y se devuelve un token de sesión.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from rest_framework.views import APIView

User = get_user_model()


class GoogleAuthView(APIView):
    """POST /api/v1/auth/google/

    Body:     {"credential": "<ID token de Google>"}
    Respuesta: {"token": "<token de sesión>", "user": {...}}
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

        token, _ = Token.objects.get_or_create(user=user)

        return Response(
            {
                "token": token.key,
                "user": {
                    "id": user.id,
                    "email": user.email,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    # URL pública del avatar en el CDN de Google. No se
                    # persiste: Google puede rotarla, así que se refresca
                    # en cada login.
                    "picture": claims.get("picture", ""),
                },
            }
        )
