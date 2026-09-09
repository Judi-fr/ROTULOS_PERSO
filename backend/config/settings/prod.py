"""Configuración de producción."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403

DEBUG = False

# ALLOWED_HOSTS ya se lee del .env en base.py (lista explícita separada por
# coma), con default ["localhost", "127.0.0.1"]. Achica el riesgo de un
# despliegue mal configurado: en producción nunca puede quedar en ["*"]
# (eso es solo para dev.py).
if ALLOWED_HOSTS == ["*"]:  # noqa: F405
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS no puede ser '*' en producción: definí los "
        "hosts/dominios reales en la variable ALLOWED_HOSTS del .env."
    )

# ---------------------------------------------------------------------------
# CORS: únicamente los orígenes explícitos de CORS_ALLOWED_ORIGINS (ya
# leídos del .env en base.py). CORS_ALLOW_ALL_ORIGINS NUNCA se activa acá
# (eso es solo dev.py, para no pelear con el frontend local).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Base de datos: en producción siempre Postgres vía DATABASE_URL (las
# variables POSTGRES_* ya están en el .env, ver docker-compose.yml). A
# diferencia de base.py, acá NO hay fallback a SQLite: si falta la variable,
# django-environ tira ImproperlyConfigured en vez de arrancar contra un
# sqlite local por accidente.
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db("DATABASE_URL"),  # noqa: F405
}

# ---------------------------------------------------------------------------
# REST Framework: solo JSONRenderer, sin la API navegable que dev.py agrega.
# ---------------------------------------------------------------------------
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
]

# ---------------------------------------------------------------------------
# Seguridad: en producción todo llega por HTTPS. Todo se lee del .env con
# defaults ya seguros, así un despliegue sin variables extra sigue quedando
# protegido (no hace falta "acordarse" de setear cada una).
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)  # noqa: F405
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)  # noqa: F405
SECURE_HSTS_SECONDS = env.int(  # noqa: F405
    "SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 30  # 30 días
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(  # noqa: F405
    "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True
)
# El proxy/balanceador (nginx, etc.) delante de gunicorn manda esta cabecera
# para indicarle a Django que la conexión original del cliente ya era HTTPS
# (el proxy es quien termina TLS). El nombre de la cabecera es configurable
# por si el proxy real usa otro; el valor esperado siempre es "https".
SECURE_PROXY_SSL_HEADER = (
    env("SECURE_PROXY_SSL_HEADER_NAME", default="HTTP_X_FORWARDED_PROTO"),  # noqa: F405
    "https",
)

# ---------------------------------------------------------------------------
# Logging: a consola (la captura el proceso del servidor — gunicorn/systemd/
# docker), nivel WARNING para no ensuciar con ruido de INFO/DEBUG.
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
}
