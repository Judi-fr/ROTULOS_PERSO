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

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from rest_framework import serializers, status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record

from .models import GroupRolePermission, RolePermission

User = get_user_model()

# Mismo orden canónico que ROLE_CHOICES / VALID_ROLES.
VALID_ROLES = ("admin", "designer", "operator", "subscriber")

ROLE_LABELS = {
    "admin": "Administrador",
    "designer": "Diseñador",
    "operator": "Operador",
    "subscriber": "Suscriptor",
}

# Nombres de roles que NO se pueden crear ni eliminar. Incluye los roles
# canónicos y los alias legacy que el sistema reconoce (grupos sembrados por
# 0001_seed_roles, "user" legacy -> subscriber, variantes en español, etc.).
PROTECTED_ROLE_NAMES = frozenset(
    {
        # Roles canónicos del sistema.
        "admin",
        "designer",
        "operator",
        "subscriber",
        # Alias legacy -> admin.
        "administrador",
        "administradores",
        "administrator",
        # Alias legacy -> designer.
        "diseñador",
        "diseñadores",
        "disenador",
        "disenadores",
        # Alias legacy -> operator.
        "operador",
        "operadores",
        # Rol legado genérico -> subscriber.
        "user",
    }
)

ROLE_NAME_ERROR = "El nombre del rol es obligatorio."


def normalize_role_name(name):
    """Normaliza un nombre de rol a minúsculas sin espacios."""
    return (name or "").strip().lower()


def is_protected_role_name(name):
    """Indica si un nombre (sin normalizar) corresponde a un rol protegido."""
    return normalize_role_name(name) in PROTECTED_ROLE_NAMES


def is_basic_role_group(group):
    """True si el Group corresponde a uno de los roles canónicos."""
    return bool(group) and group.name in VALID_ROLES

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
    """GET/POST /api/v1/auth/roles/

    GET:  devuelve todos los roles (los cuatro básicos + personalizados).
          Los básicos conservan el orden canónico y aparecen primero; los
          personalizados se ordenan alfabéticamente después.
    POST: crea un rol nuevo (Django Group) sin permisos asignados.
          Solo admin, nombre obligatorio, normalizado y sin colisiones
          con roles existentes ni nombres protegidos.
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        groups = list(Group.objects.filter(name__in=VALID_ROLES))
        groups = sorted(groups, key=lambda g: VALID_ROLES.index(g.name))
        custom_groups = (
            Group.objects.exclude(name__in=VALID_ROLES)
            .order_by("name")
        )
        all_roles = groups + list(custom_groups)
        return Response(RoleSerializer(all_roles, many=True).data)

    def post(self, request):
        raw_name = request.data.get("name")
        if raw_name is None or str(raw_name).strip() == "":
            return Response(
                {"name": ROLE_NAME_ERROR},
                status=status.HTTP_400_BAD_REQUEST,
            )

        normalized = normalize_role_name(raw_name)

        # No se pueden crear roles con nombres protegidos/legacy.
        if is_protected_role_name(normalized):
            return Response(
                {"name": f"El nombre '{normalized}' está reservado y no puede usarse para un rol personalizado."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Evitar duplicados sin importar mayúsculas/minúsculas ni espacios.
        if Group.objects.filter(name__iexact=normalized).exists():
            return Response(
                {"name": f"Ya existe un rol llamado '{normalized}'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        group = Group.objects.create(name=normalized)
        record(
            request,
            category="roles",
            action="role.create",
            target=group,
            target_type="role",
            target_repr=group.name,
            changes={"name": {"from": None, "to": group.name}},
        )
        return Response(
            RoleSerializer(group).data,
            status=status.HTTP_201_CREATED,
        )


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

    Aplica a los roles básicos y también a los personalizados creados vía
    POST /api/v1/auth/roles/.
    """

    permission_classes = [IsAdminUser]

    def _get_group_or_404(self, role_id):
        # Cualquier Group puede ser un rol (básico o personalizado).
        group = Group.objects.filter(pk=role_id).first()
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
        previous_keys = set(
            GroupRolePermission.objects.filter(group=group).values_list(
                "permission__key", flat=True
            )
        )
        GroupRolePermission.objects.filter(group=group).delete()
        permissions = list(RolePermission.objects.filter(key__in=keys))
        GroupRolePermission.objects.bulk_create(
            [GroupRolePermission(group=group, permission=p) for p in permissions],
            ignore_conflicts=True,
        )

        new_keys = set(keys)
        record(
            request,
            category="roles",
            action="role.permissions_update",
            target=group,
            target_type="role",
            target_repr=group.name,
            changes={
                "added": sorted(new_keys - previous_keys),
                "removed": sorted(previous_keys - new_keys),
            },
        )

        links = GroupRolePermission.objects.filter(group=group).select_related("permission")
        result_permissions = [link.permission for link in links]
        return Response(
            {
                "role": RoleSerializer(group).data,
                "permissions": RolePermissionSerializer(result_permissions, many=True).data,
            }
        )


class RoleDetailView(APIView):
    """DELETE /api/v1/auth/roles/<role_id>/

    Elimina un rol personalizado. Los roles básicos (admin, designer,
    operator, subscriber) están protegidos y no pueden eliminarse.

    Al eliminar un rol personalizado:
    - Los usuarios que pertenecían SOLO a ese rol pasan a ``subscriber``.
    - Los usuarios que también pertenecían a otros roles conservan los demás
      Groups (no se toca ``is_staff`` ni ``is_superuser``).
    - Se eliminan las asignaciones ``GroupRolePermission`` del rol.
    - Se elimina el Group.
    - NO se eliminan usuarios.
    """

    permission_classes = [IsAdminUser]

    def delete(self, request, role_id):
        group = Group.objects.filter(pk=role_id).first()
        if group is None:
            return Response(
                {"detail": "Rol no encontrado."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Los roles básicos son irremovibles, incluso si se intenta manipular
        # directamente el endpoint.
        if is_basic_role_group(group) or is_protected_role_name(group.name):
            return Response(
                {"detail": f"El rol '{group.name}' está protegido y no puede eliminarse."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subscriber_group = Group.objects.filter(name="subscriber").first()
        if subscriber_group is None:
            subscriber_group = Group.objects.create(name="subscriber")

        # 1. Reasignar usuarios: quienes pertenecían a este rol conservan sus
        #    demás Groups y además reciben ``subscriber`` como rol efectivo.
        #    Si ya tenían otro Group de rol válido, seguirán con ese rol; la
        #    re-asignación de subscriber es segura y no modifica is_staff ni
        #    is_superuser.
        role_user_ids = group.user_set.values_list("id", flat=True)
        for user in User.objects.filter(id__in=role_user_ids):
            user.groups.add(subscriber_group)

        # 2. Eliminar asignaciones de permisos del rol (cascade implícito al
        #    borrar el Group, pero lo hacemos explícito para claridad).
        GroupRolePermission.objects.filter(group=group).delete()

        role_name, role_id = group.name, group.pk
        record(
            request,
            category="roles",
            action="role.delete",
            target_type="role",
            target_id=str(role_id),
            target_repr=role_name,
        )

        # 3. Eliminar el Group (los usuarios NO se borran).
        group.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)
