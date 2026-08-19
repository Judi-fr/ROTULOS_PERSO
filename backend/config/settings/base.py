"""
Configuración base del proyecto ROTULOS_PERSO.

Ajustes comunes a todos los entornos. Los valores sensibles o que varían
por entorno se leen desde variables de entorno / archivo .env.
"""

from datetime import timedelta
from pathlib import Path

import environ

# BASE_DIR apunta a la raíz del repo (dos niveles arriba de config/settings/)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CORS_ALLOWED_ORIGINS=(list, []),
)

# Lee el archivo .env de la raíz si existe (en producción se usan
# variables de entorno reales y este archivo no está presente)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")

# Se lee desde la variable de entorno ALLOWED_HOSTS (lista separada por comas).
# En dev.py se sobreescribe con ["*"]; en prod DEBE venir del .env con los
# dominios/IPs reales. El default cubre el desarrollo local sin .env.
ALLOWED_HOSTS = env(
    "ALLOWED_HOSTS",
    default=["localhost", "127.0.0.1", "192.168.1.34", "100.105.137.61"],
)

# ---------------------------------------------------------------------------
# Aplicaciones
# ---------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",  # revocación de refresh tokens
    "corsheaders",
]

LOCAL_APPS = [
    "apps.accounts",    # autenticación (Google Sign-In)
    "apps.documents",   # carga y gestión de archivos (imágenes / PDF)
    "apps.processing",  # agente embebido: convierte el documento a formato código
    "apps.labels",      # rótulos generados, plantillas e impresión
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # CorsMiddleware debe ir antes de CommonMiddleware
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------

DATABASES = {
    # DATABASE_URL permite cambiar de motor sin tocar código,
    # p. ej. postgres://user:pass@host:5432/rotulos
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    # Toda la API se autentica con JWT (djangorestframework-simplejwt).
    # El frontend manda el access token en el header:
    #   Authorization: Bearer <access>
    # Tanto el login con Google como el de email/contraseña emiten JWT,
    # así el frontend maneja un único tipo de token sin importar el método.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",  # necesario para subir imágenes/PDF
        "rest_framework.parsers.FormParser",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    # Cerrado por defecto: cada endpoint exige usuario autenticado salvo que
    # declare `permission_classes = [AllowAny]` explícitamente (login, registro,
    # Google, health). Así un endpoint nuevo nunca queda público por descuido.
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # Rate limiting. El scope "login" se aplica a mano en las vistas de auth
    # (ver LoginRateThrottle); "anon"/"user" son los límites generales.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "1000/min",
        "login": "5/min",
        # Pedir/confirmar reset de contraseña: frena el email bombing a una
        # víctima y la fuerza bruta sobre el token del link.
        "password_reset": "5/min",
    },
}

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")

# ---------------------------------------------------------------------------
# Google OAuth / Sign-In
# ---------------------------------------------------------------------------

# client_id / client_secret creados en Google Cloud Console (ver .env).
# El client_id se usa como "audience" al verificar el ID token en el Flujo A.
# El client_secret solo hace falta si se implementa el Flujo B (redirect/code).
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", default="")
GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET", default="")
GOOGLE_REDIRECT_URI = env("GOOGLE_REDIRECT_URI", default="")

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

# Por defecto SMTP (producción). En dev.py se sobreescribe con el backend de
# consola para no necesitar un servidor de correo real. Los datos del SMTP se
# leen del .env; con EMAIL_BACKEND se puede forzar otro backend sin tocar código.
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)

# Remitente de los correos transaccionales (reset de contraseña, etc.).
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default="ROTULOS <no-reply@rotulos.local>"
)

# Base del frontend (SPA) para armar links que van en los correos, p. ej. el
# de reset apunta a {FRONTEND_URL}/reset-password?uid=...&token=...
FRONTEND_URL = env("FRONTEND_URL", default="http://localhost:5173")

# URL de la página que resuelve el reset (a la que apunta el enlace del correo).
# Por defecto es {FRONTEND_URL}/reset-password (la ruta de la SPA), pero se puede
# sobreescribir desde el .env; p. ej. mientras no exista el frontend, apuntarla a
# la página de prueba de oauth-test (http://localhost:3000/reset-password.html).
PASSWORD_RESET_URL = env(
    "PASSWORD_RESET_URL", default=f"{FRONTEND_URL}/reset-password"
)

# Validez del token de reset de contraseña (default de Django: 3 días).
PASSWORD_RESET_TIMEOUT = env.int("PASSWORD_RESET_TIMEOUT", default=60 * 60 * 24)

# ---------------------------------------------------------------------------
# JWT (djangorestframework-simplejwt)
# ---------------------------------------------------------------------------

# El access token es de vida corta y viaja en cada petición; cuando expira,
# el frontend usa el refresh (vida larga) para obtener uno nuevo sin volver
# a pedir credenciales. La firma se hace con SECRET_KEY.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    # Rotación: cada uso del endpoint de refresh emite un refresh NUEVO y
    # manda el anterior a la blacklist. Si un refresh robado se reutiliza
    # después de haber sido rotado, la petición falla (401) y el robo queda
    # en evidencia. Requiere la app token_blacklist y correr las migraciones.
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}

# ---------------------------------------------------------------------------
# Validación de contraseñas
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Internacionalización
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "es-ar"
TIME_ZONE = "America/Argentina/Buenos_Aires"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Archivos estáticos y multimedia
# ---------------------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Los documentos subidos (imágenes / PDF) se guardan acá
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
