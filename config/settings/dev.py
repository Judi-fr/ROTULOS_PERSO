"""Configuración de desarrollo."""

from .base import *  # noqa: F401,F403

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# En desarrollo se permite cualquier origen para no pelear con el frontend local
CORS_ALLOW_ALL_ORIGINS = True

# API navegable de DRF solo en desarrollo
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [  # noqa: F405
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]
