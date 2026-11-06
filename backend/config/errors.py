"""Errores de la API siempre en JSON.

DRF ya contesta en JSON sus propios errores (validación, permisos, 404 de un
objeto) y el frontend lee esa forma (``getErrorMessage`` en utils.js), así que
eso no se toca. Lo que salía en HTML era lo inesperado:

- una excepción no prevista en una vista: Django devolvía su página de error
  500, que el frontend no puede leer y el comerciante ve como "Error";
- una ruta de la API que no existe: la página 404 de Django.

Solo para rutas ``/api/``: el admin y el resto siguen con las páginas de
Django. Con ``DEBUG`` encendido la excepción sigue su camino (la página de
depuración de Django es más útil mientras se desarrolla).

Cada 500 se registra con un código corto (``error_id``) que viaja en la
respuesta: el comerciante lo puede citar y se encuentra en el log.
"""

from __future__ import annotations

import logging
import math
import uuid

from django.conf import settings
from django.http import JsonResponse
from django.views import defaults
from rest_framework.exceptions import Throttled
from rest_framework.views import exception_handler

# Bajo ``apps.`` para salir por el handler con formato (ver LOGGING en base.py).
logger = logging.getLogger("apps.errors")

API_PREFIX = "/api/"
SERVER_ERROR_DETAIL = "Ocurrió un error inesperado. Si se repite, avisá a soporte con este código: {error_id}."


def _is_api(request):
    return request.path.startswith(API_PREFIX)


def _server_error_response(error_id):
    return JsonResponse(
        {"detail": SERVER_ERROR_DETAIL.format(error_id=error_id), "error_id": error_id},
        status=500,
    )


class ApiExceptionMiddleware:
    """Convierte una excepción no prevista de una vista de la API en un 500
    JSON con ``error_id``, después de registrarla con su traza."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        if not _is_api(request) or settings.DEBUG:
            return None
        error_id = uuid.uuid4().hex[:12]
        logger.exception(
            "Error no previsto [%s] en %s %s", error_id, request.method, request.path,
            extra={"error_id": error_id, "path": request.path, "method": request.method},
        )
        response = _server_error_response(error_id)
        # Ya quedó registrado acá, con la traza y el código: sin esto
        # django.request agrega un "Internal Server Error" sin ninguno de los dos.
        response._has_been_logged = True
        return response


def api_exception_handler(exc, context):
    """El de DRF, con el límite de pedidos en palabras de quien lo lee: su texto
    es "Solicitud fue regulada (throttled)", y lo ve quien se equivoca al entrar."""
    response = exception_handler(exc, context)
    if response is not None and isinstance(exc, Throttled):
        wait = math.ceil(exc.wait or 0)
        when = f"en {wait} segundos" if 0 < wait < 90 else f"en {math.ceil(wait / 60)} minutos" if wait else "en un rato"
        response.data = {"detail": f"Demasiados intentos seguidos. Probá de nuevo {when}."}
    return response


def page_not_found(request, exception, template_name=defaults.ERROR_404_TEMPLATE_NAME):
    if _is_api(request):
        return JsonResponse({"detail": "Esa ruta de la API no existe."}, status=404)
    return defaults.page_not_found(request, exception, template_name)


def server_error(request, template_name=defaults.ERROR_500_TEMPLATE_NAME):
    # Llega acá lo que falló fuera de una vista (en un middleware, por
    # ejemplo): Django ya lo registró, solo falta que la respuesta sea JSON.
    if _is_api(request):
        return _server_error_response(uuid.uuid4().hex[:12])
    return defaults.server_error(request, template_name)
