"""Tokens JWT en cookies HttpOnly para el frontend web (T#364/T#366).

Antes el frontend guardaba el access y el refresh en ``localStorage``, donde
cualquier script que corra en la página (un XSS, una dependencia
comprometida) los puede leer y llevarse. En una cookie HttpOnly el navegador
los manda solo y JavaScript no los ve.

**Quién usa cookies lo pide con el header ``X-Auth-Mode: cookie``** (lo pone
``frontend/assets/js/auth.js`` en cada request). Con ese header, el login, el
registro, Google y el refresh dejan los tokens en cookies y los SACAN del
cuerpo de la respuesta: si siguieran en el JSON, un script podría pedir un
refresh y leerlos igual. Sin el header todo responde como siempre (tokens en
el cuerpo, ``Authorization: Bearer``): así siguen andando los clientes de la
API que no son un navegador, y los tests.

Decisiones (pedidas en T#366):

- **Nombres**: ``rotulos_access`` y ``rotulos_refresh``.
- **Dominio**: ninguno (cookie del host exacto que respondió) salvo
  ``AUTH_COOKIE_DOMAIN``. En producción Caddy sirve frontend y API bajo un
  solo dominio, así que no hace falta compartirla con subdominios.
- **Path**: el access va a ``/api/`` (no viaja con el frontend ni con
  ``/media/``); el refresh solo a ``/api/v1/auth/``, que es donde se usa
  (refresh y logout): es el token que dura 7 días, cuanto menos viaje mejor.
- **HttpOnly** siempre; **SameSite=Lax**; **Secure** según
  ``AUTH_COOKIE_SECURE`` (por defecto prendido salvo con ``DEBUG``: en
  desarrollo la app corre por HTTP).
- **Vida**: la de cada token (``SIMPLE_JWT``: 15 minutos y 7 días), así el
  navegador las descarta cuando ya no sirven.

**Falsificación de requests (CSRF)**: una cookie viaja sola, así que otro
sitio podría intentar disparar una acción con la sesión del usuario.
SameSite=Lax ya impide que la manden los requests de OTRO sitio, y además
``CookieJWTAuthentication`` exige ``X-Auth-Mode`` en los métodos que
modifican: un header propio obliga al navegador a una consulta previa
(preflight) que el CORS de la API solo responde a los orígenes permitidos.
"""

from __future__ import annotations

from django.conf import settings
from rest_framework_simplejwt.settings import api_settings as jwt_settings

MODE_HEADER = "HTTP_X_AUTH_MODE"
COOKIE_MODE = "cookie"
TOKEN_KEYS = ("access", "refresh")


def access_cookie_name():
    return settings.AUTH_COOKIE_ACCESS_NAME


def refresh_cookie_name():
    return settings.AUTH_COOKIE_REFRESH_NAME


def wants_cookies(request) -> bool:
    return request.META.get(MODE_HEADER, "").strip().lower() == COOKIE_MODE


def _cookie_options(path):
    return {
        "path": path,
        "domain": settings.AUTH_COOKIE_DOMAIN or None,
        "secure": settings.AUTH_COOKIE_SECURE,
        "httponly": True,
        "samesite": settings.AUTH_COOKIE_SAMESITE,
    }


def set_auth_cookies(response, *, access=None, refresh=None):
    if access:
        response.set_cookie(
            access_cookie_name(),
            access,
            max_age=int(jwt_settings.ACCESS_TOKEN_LIFETIME.total_seconds()),
            **_cookie_options(settings.AUTH_COOKIE_ACCESS_PATH),
        )
    if refresh:
        response.set_cookie(
            refresh_cookie_name(),
            refresh,
            max_age=int(jwt_settings.REFRESH_TOKEN_LIFETIME.total_seconds()),
            **_cookie_options(settings.AUTH_COOKIE_REFRESH_PATH),
        )
    return response


def clear_auth_cookies(response):
    for name, path in (
        (access_cookie_name(), settings.AUTH_COOKIE_ACCESS_PATH),
        (refresh_cookie_name(), settings.AUTH_COOKIE_REFRESH_PATH),
    ):
        response.delete_cookie(
            name, path=path, domain=settings.AUTH_COOKIE_DOMAIN or None, samesite=settings.AUTH_COOKIE_SAMESITE
        )
    return response


def deliver_tokens(request, response):
    """En modo cookie, pasa ``access``/``refresh`` del cuerpo de ``response``
    a cookies y los quita del cuerpo. Sin el header, no toca nada."""
    if not wants_cookies(request) or not isinstance(getattr(response, "data", None), dict):
        return response
    tokens = {key: response.data.pop(key) for key in TOKEN_KEYS if key in response.data}
    set_auth_cookies(response, **tokens)
    return response


def refresh_from_request(request):
    """El refresh del cuerpo (clientes de la API) o, si no vino, el de la cookie."""
    return request.data.get("refresh") or request.COOKIES.get(refresh_cookie_name()) or ""
