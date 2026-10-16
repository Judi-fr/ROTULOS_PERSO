"""Autenticación JWT + enforcement de "forzar cambio de contraseña".

``JWTAuthenticationWithPasswordPolicy`` es el único choke point que cubre
TODA la API: muchas vistas ya pisan ``permission_classes`` explícitamente
(ver ``viewsets.py``, ``role_permission_views.py``), así que agregar un
``BasePermission`` a ``DEFAULT_PERMISSION_CLASSES`` no llegaría a esas
vistas. La autenticación, en cambio, corre siempre (ninguna vista de este
proyecto pisa ``authentication_classes``), así que verificar el flag acá
garantiza que no se pueda "saltear" pegándole directo a un endpoint
cualquiera con un access token válido.

El access token se lee del header ``Authorization: Bearer`` (clientes de la
API) o, si no vino, de la cookie HttpOnly del frontend web (ver
``auth_cookies.py``).
"""

from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from .auth_cookies import access_cookie_name, wants_cookies
from .models import PasswordChangeRequirement

# Endpoints alcanzables aunque el usuario tenga must_change_password=True:
# el propio cambio de contraseña, ver el propio perfil (para no romper la
# UI mientras se muestra la pantalla obligatoria) y logout (para poder
# salir en vez de quedar atrapado). Se matchea por (método, path exacto).
_ALLOWED_WHILE_PENDING = {
    ("POST", "/api/v1/auth/me/change-password/"),
    ("GET", "/api/v1/auth/me/"),
    ("POST", "/api/v1/auth/logout/"),
}


def user_must_change_password(user):
    """True si el usuario tiene pendiente un cambio de contraseña forzado."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    requirement = PasswordChangeRequirement.objects.filter(user=user).first()
    return bool(requirement and requirement.must_change_password)


class JWTAuthenticationWithPasswordPolicy(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            result = self._authenticate_cookie(request)
        if result is None:
            return None

        user, validated_token = result
        if user_must_change_password(user):
            if (request.method, request.path) not in _ALLOWED_WHILE_PENDING:
                raise PermissionDenied(
                    {
                        "detail": "Tenés que cambiar tu contraseña antes de continuar.",
                        "must_change_password": True,
                    }
                )
        return result

    def _authenticate_cookie(self, request):
        raw = request.COOKIES.get(access_cookie_name())
        if not raw:
            return None
        try:
            validated_token = self.get_validated_token(raw.encode())
        except (InvalidToken, TokenError):
            # Una cookie vencida no debe trabar el login ni el refresh (que
            # son públicos y la reciben igual, por el path /api/): se la
            # ignora, y un endpoint protegido contesta 401 como sin sesión.
            return None
        # Defensa contra requests falsificados desde otro sitio (ver
        # auth_cookies.py): lo que modifica exige el header propio.
        if request.method not in SAFE_METHODS and not wants_cookies(request):
            raise PermissionDenied("Falta el header X-Auth-Mode: cookie.")
        return self.get_user(validated_token), validated_token
