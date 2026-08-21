"""Permisos DRF respaldados por la asignación GroupRolePermission."""

from rest_framework.permissions import BasePermission

from .permissions_map import user_has_permission


class HasRolePermission(BasePermission):
    """Exige un permiso atómico del catálogo asignado al rol efectivo."""

    message = "No tenés permisos para realizar esta acción."

    def __init__(self, permission_key):
        self.permission_key = permission_key

    def has_permission(self, request, view):
        return user_has_permission(request.user, self.permission_key)
