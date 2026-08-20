"""Administración de roles (Django Groups) y permisos (RolePermission).

Expone el catálogo de roles y permisos y permite reasignar los permisos de
cada rol. Respetando la arquitectura actual:

- El ROL es un Django ``Group`` (fuente de verdad). No se crea un modelo ``Role``.
- Los permisos viven en ``RolePermission`` y su asignación a cada Group en
  ``GroupRolePermission`` (tabla intermedia con unicidad Group+Permission).

Todos los endpoints requieren ``IsAdminUser`` en esta etapa. Una vez que el
nuevo sistema de permisos gobierne los endpoints, se podrá afinar la
protección (p. ej. un permiso ``roles.edit_permissions`` que hoy NO forma
parte del catálogo y por eso no se inventa).
"""

from django.contrib.auth.models import Group

from rest_framework import serializers, status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import GroupRolePermission, RolePermission

# Mismo orden canónico que ROLE_CHOICES / VALID_ROLES.
VALID_ROLES = ("admin", "designer", "operator", "subscriber")

ROLE_LABELS = {
    "admin": "Administrador",
    "designer": "Diseñador",
    "operator": "Operador",
    "subscriber": "Suscriptor",
}

# Protección del administrador: el rol admin nunca debe quedar sin permisos,
# para no dejar al sistema sin la posibilidad de administrar roles. Mientras
# no exista un permiso específico ``roles.edit_permissions`` en el catálogo,
# se impide solamente que el payload deje al rol admin vacío.
PROTECTED_ROLE_NO_PERMISSIONS_MSG = (
    "El rol 'admin' no puede quedar sin permisos asignados."
)


class RoleSerializer(serializers.ModelSerializer):
    """Serializa un Group como rol del sistema (solo lectura)."""

    name = serializers.CharField(read_only=True)
    label = serializers.SerializerMethodField()

    class Meta:
        model = Group
        fields = ["id", "name", "label"]

    def get_label(self, obj):
        return ROLE_LABELS.get(obj.name, obj.name.capitalize())


class RolePermissionSerializer(serializers.ModelSerializer):
    """Serializa un permiso del catálogo (solo lectura)."""

    class Meta:
        model = RolePermission
        fields = ["id", "key", "name", "category"]


class RoleListView(APIView):
    """GET /api/v1/auth/roles/

    Devuelve los cuatro roles fijos del sistema. No permite crear ni
    eliminar roles: los cuatro son fijos.
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        groups = Group.objects.filter(name__in=VALID_ROLES)
        roles = sorted(groups, key=lambda g: VALID_ROLES.index(g.name))
        return Response(RoleSerializer(roles, many=True).data)


class PermissionCatalogView(APIView):
    """GET /api/v1/auth/permissions/

    Devuelve el catálogo completo de permisos del sistema.
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        permissions = RolePermission.objects.all()
        return Response(RolePermissionSerializer(permissions, many=True).data)


class RolePermissionsView(APIView):
    """GET/PUT /api/v1/auth/roles/<role_id>/permissions/

    GET:  devuelve el rol y sus permisos actuales.
    PUT:  reemplaza la asignación de permisos del rol por exactamente los
          permisos enviados. Valida que todos existan ANTES de modificar
          nada; si alguno no existe -> 400 sin tocar la base.
    """

    permission_classes = [IsAdminUser]

    def _get_group_or_404(self, role_id):
        group = (
            Group.objects.filter(pk=role_id, name__in=VALID_ROLES).first()
        )
        if group is None:
            return None
        return group

    def get(self, request, role_id):
        group = self._get_group_or_404(role_id)
        if group is None:
            return Response({"detail": "Rol no encontrado."}, status=status.HTTP_404_NOT_FOUND)

        links = GroupRolePermission.objects.filter(group=group).select_related("permission")
        permissions = [link.permission for link in links]
        return Response(
            {
                "role": RoleSerializer(group).data,
                "permissions": RolePermissionSerializer(permissions, many=True).data,
            }
        )

    def put(self, request, role_id):
        group = self._get_group_or_404(role_id)
        if group is None:
            return Response({"detail": "Rol no encontrado."}, status=status.HTTP_404_NOT_FOUND)

        keys = request.data.get("permissions")
        if not isinstance(keys, list):
            return Response(
                {"permissions": "Se esperaba una lista de permisos."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        keys = [str(k).strip() for k in keys]

        # Validar ANTES de modificar: todos los permisos deben existir.
        known = set(
            RolePermission.objects.filter(key__in=keys).values_list("key", flat=True)
        )
        unknown = set(keys) - known
        if unknown:
            return Response(
                {"permissions": f"Permisos inexistentes: {', '.join(sorted(unknown))}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Protección del administrador: el rol admin no puede quedar vacío.
        if group.name == "admin" and not keys:
            return Response(
                {"permissions": PROTECTED_ROLE_NO_PERMISSIONS_MSG},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Reemplazo atómico de la asignación.
        GroupRolePermission.objects.filter(group=group).delete()
        permissions = list(RolePermission.objects.filter(key__in=keys))
        GroupRolePermission.objects.bulk_create(
            [GroupRolePermission(group=group, permission=p) for p in permissions],
            ignore_conflicts=True,
        )

        links = GroupRolePermission.objects.filter(group=group).select_related("permission")
        result_permissions = [link.permission for link in links]
        return Response(
            {
                "role": RoleSerializer(group).data,
                "permissions": RolePermissionSerializer(result_permissions, many=True).data,
            }
        )