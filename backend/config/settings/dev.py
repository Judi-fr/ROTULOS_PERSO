"""Configuración de desarrollo."""

from .base import *  # noqa: F401,F403

DEBUG = True

# En desarrollo aceptamos cualquier host: así el backend responde tanto en
# localhost como en la IP de la LAN (192.168.x) o de Tailscale (100.x) cuando
# se accede desde otra máquina, sin tener que listar cada IP a mano.
# Solo para DEV — en prod se usa una lista explícita.
ALLOWED_HOSTS = ["*"]

# En desarrollo se permite cualquier origen para no pelear con el frontend local
CORS_ALLOW_ALL_ORIGINS = True

# API navegable de DRF solo en desarrollo
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]

# En desarrollo, por defecto los correos (p. ej. el link de reset de contraseña)
# se imprimen en la consola del runserver en vez de enviarse: así se prueba el
# flujo sin configurar un servidor SMTP.
# Si en el .env se define EMAIL_BACKEND (p. ej. el backend SMTP), ese gana: así
# se puede enviar correo real desde dev sin tocar código.
EMAIL_BACKEND = env(  # noqa: F405
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
