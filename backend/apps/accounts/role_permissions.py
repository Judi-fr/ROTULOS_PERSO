"""Permisos DRF respaldados por la asignación GroupRolePermission."""

from rest_framework.permissions import BasePermission

from .permissions_map import TEAM_SCOPED_PERMISSIONS, user_has_permission


class HasRolePermission(BasePermission):
    """Exige un permiso atómico del catálogo asignado al rol efectivo."""

    message = "No tenés permisos para realizar esta acción."

    def __init__(self, permission_key):
        self.permission_key = permission_key

    def has_permission(self, request, view):
        if user_has_permission(request.user, self.permission_key):
            return True
        # Sin comercio, lo del trabajo del comercio responde ``no_team`` (y no
        # un 403 genérico): el frontend manda a crear uno o a sumarse.
        if self.permission_key in TEAM_SCOPED_PERMISSIONS and getattr(request.user, "is_authenticated", False):
            from .teams import NoTeam, membership

            if membership(request.user) is None:
                raise NoTeam()
        return False
