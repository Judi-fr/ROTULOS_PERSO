"""Autenticación JWT + enforcement de "forzar cambio de contraseña".

``JWTAuthenticationWithPasswordPolicy`` es el único choke point que cubre
TODA la API: muchas vistas ya pisan ``permission_classes`` explícitamente
(ver ``viewsets.py``, ``role_permission_views.py``), así que agregar un
``BasePermission`` a ``DEFAULT_PERMISSION_CLASSES`` no llegaría a esas
vistas. La autenticación, en cambio, corre siempre (ninguna vista de este
proyecto pisa ``authentication_classes``), así que verificar el flag acá
garantiza que no se pueda "saltear" pegándole directo a un endpoint
cualquiera con un access token válido.
"""

from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.authentication import JWTAuthentication

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
