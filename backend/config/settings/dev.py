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
