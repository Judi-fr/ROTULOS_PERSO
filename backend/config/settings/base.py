"""
Configuración base del proyecto ROTULOS_PERSO.

Ajustes comunes a todos los entornos. Los valores sensibles o que varían
por entorno se leen desde variables de entorno / archivo .env.
"""

from datetime import timedelta
from pathlib import Path
import os

# BASE_DIR apunta a la raíz del repo (dos niveles arriba de config/settings/)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Lee variables de entorno desde archivo .env si existe
ENV_FILE = BASE_DIR / ".env"
if ENV_FILE.exists():
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

# Variables de entorno con valores por defecto
SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-dev-key-do-not-use")
DEBUG = os.environ.get("DEBUG", "False").lower() == "true"
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:8001")
ADMIN_CREATED_USER_PASSWORD = os.environ.get("ADMIN_CREATED_USER_PASSWORD", "TempPass123")
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,192.168.1.34,100.105.137.61").split(",")

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
    "corsheaders",
    "rest_framework_simplejwt.token_blacklist",
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
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(BASE_DIR / "db.sqlite3"),
    },
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
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.AllowAny",
    ],
}

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

CORS_ALLOWED_ORIGINS = os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")

# ---------------------------------------------------------------------------
# Google OAuth / Sign-In
# ---------------------------------------------------------------------------

# client_id / client_secret creados en Google Cloud Console (ver .env).
# El client_id se usa como "audience" al verificar el ID token en el Flujo A.
# El client_secret solo hace falta si se implementa el Flujo B (redirect/code).
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "")

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

# En desarrollo, el backend por defecto imprime el correo en la consola.
# Para mandar mail real, se puede configurar SMTP desde .env.
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.human-log.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True").lower() == "true"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "acruzgarcia@human-log.com")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "Agus?01!")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "acruzgarcia@human-log.com")

# ---------------------------------------------------------------------------
# JWT (djangorestframework-simplejwt)
# ---------------------------------------------------------------------------

# El access token es de vida corta y viaja en cada petición; cuando expira,
# el frontend usa el refresh (vida larga) para obtener uno nuevo sin volver
# a pedir credenciales. La firma se hace con SECRET_KEY.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "BLACKLIST_AFTER_ROTATION": True,
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
