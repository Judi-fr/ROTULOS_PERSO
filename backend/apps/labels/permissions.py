"""Permisos de la app labels.

El catálogo de variables es un recurso compartido: una variable que alguien da
de alta aparece para todas las plantillas y todos los usuarios. Por eso leerlo
lo puede hacer cualquier usuario autenticado (el editor necesita la lista para
armar sus selectores) pero escribirlo queda reservado a administradores.

Se acepta como administrador tanto a quien tiene ``is_staff`` —la vía que ya
usa la gestión de usuarios en ``apps.accounts``— como a quien pertenece al
grupo ``administradores``. La migración que siembra los roles declara que ambos
son equivalentes, pero son dos marcas independientes en la base: un usuario
puede tener el grupo sin ``is_staff``, y comprobar solo una de las dos dejaría
fuera a administradores legítimos.
"""

from rest_framework.permissions import SAFE_METHODS, BasePermission

GRUPO_ADMINISTRADORES = "administradores"


def es_administrador(user):
    """¿El usuario puede administrar recursos compartidos de la app?"""
    if not user or not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    return user.groups.filter(name=GRUPO_ADMINISTRADORES).exists()


class EsAdministrador(BasePermission):
    """Exige administrador para cualquier método."""

    message = "Se requiere rol de administrador."

    def has_permission(self, request, view):
        return es_administrador(request.user)


class LecturaAutenticadaEscrituraAdministrador(BasePermission):
    """Lectura para cualquier autenticado; escritura solo para administradores."""

    message = "Solo un administrador puede modificar el catálogo de variables."

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return bool(request.user and request.user.is_authenticated)
        return es_administrador(request.user)
