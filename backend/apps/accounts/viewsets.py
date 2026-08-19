from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db.models import Q
from rest_framework import permissions, status, viewsets
from rest_framework.response import Response

from .pagination import UserAdminPagination
from .serializers import ROLE_CHOICES, UserAdminSerializer, ensure_role_groups

User = get_user_model()


class UserAdminViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by("id")
    serializer_class = UserAdminSerializer
    pagination_class = UserAdminPagination
    permission_classes = [permissions.IsAdminUser]

    def _get_status_filter(self):
        status_param = self.request.query_params.get("status", "").strip().lower()
        if status_param == "active":
            return Q(is_active=True)
        if status_param == "inactive":
            return Q(is_active=False)
        return Q()

    def _get_role_filter(self):
        role_param = self.request.query_params.get("role", "").strip().lower()
        if not role_param or role_param == "all":
            return Q()

        # Normalizar: "administrador" es alias de "admin"
        if role_param == "administrator":
            role_param = "admin"

        # "user" es alias legacy: usuarios normales sin rol admin/designer/operator
        if role_param == "user":
            special_roles = ["admin", "designer", "operator"]
            return Q(is_staff=False) & ~Q(groups__name__in=special_roles)

        if role_param in ROLE_CHOICES:
            ensure_role_groups()
            # Para admin: incluir usuarios con group Y usuarios staff efectivos
            # (sin group explícito, pero is_staff=True)
            if role_param == "admin":
                try:
                    group = Group.objects.get(name="admin")
                    return Q(groups=group) | Q(is_staff=True)
                except Group.DoesNotExist:
                    return Q(is_staff=True)

            # Para otros roles (designer, operator, subscriber): filtrar por group
            try:
                group = Group.objects.get(name=role_param)
                return Q(groups=group)
            except Group.DoesNotExist:
                return Q(pk__in=[])

        return Q()

    def _get_search_filter(self):
        search = self.request.query_params.get("search", "").strip()
        if not search:
            return Q()

        # Normalizar: "administrador" es alias de "admin" en búsquedas
        search_lower = search.lower()
        if search_lower == "administrator":
            search_lower = "admin"

        filters = (
            Q(email__icontains=search)
            | Q(username__icontains=search)
            | Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
        )

        # También buscar por rol: el término de búsqueda puede ser un nombre de rol
        role = search_lower
        if role in ROLE_CHOICES:
            ensure_role_groups()
            # Para admin: buscar usuarios con group y usuarios staff efectivos
            if role == "admin":
                try:
                    group = Group.objects.get(name="admin")
                    filters |= Q(groups=group) | Q(is_staff=True)
                except Group.DoesNotExist:
                    filters |= Q(is_staff=True)
            else:
                try:
                    group = Group.objects.get(name=role)
                    filters |= Q(groups=group)
                except Group.DoesNotExist:
                    pass

        return filters

    def _get_ordering(self, queryset):
        ordering_param = self.request.query_params.get("ordering", "").strip()
        if not ordering_param:
            return queryset.order_by("id")

        allowed_fields = {"id", "email", "first_name", "last_name", "is_active", "is_staff", "date_joined"}
        ordering_fields = []
        for raw_field in ordering_param.split(","):
            field = raw_field.strip()
            if not field:
                continue
            descending = field.startswith("-")
            field_name = field[1:] if descending else field
            if field_name in allowed_fields:
                ordering_fields.append(f"-{field_name}" if descending else field_name)

        if ordering_fields:
            return queryset.order_by(*ordering_fields)
        return queryset.order_by("id")

    def get_queryset(self):
        queryset = super().get_queryset()
        filters = self._get_status_filter() & self._get_role_filter() & self._get_search_filter()
        if filters:
            queryset = queryset.filter(filters).distinct()
        return self._get_ordering(queryset)

    def list(self, request, *args, **kwargs):
        """Lista usuarios con resumen global y metadatos de paginación."""
        response = super().list(request, *args, **kwargs)
        response.data["summary"] = {
            "total": User.objects.count(),
            "active": User.objects.filter(is_active=True).count(),
            "inactive": User.objects.filter(is_active=False).count(),
        }
        return response

    def _is_self_target(self, instance):
        return self.request.user == instance

    def _reject_self_admin_mutation(self, instance, data):
        """Impide que un admin altere su propio rol, staff o estado vía CRUD."""
        if not self._is_self_target(instance):
            return None

        if any(field in data for field in ("role", "is_staff")):
            return Response(
                {"detail": "No puede cambiar su propio rol o permisos de staff."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if any(field in data for field in ("status", "is_active")):
            return Response(
                {"detail": "No puede desactivarse a sí mismo."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    def destroy(self, request, *args, **kwargs):
        """Desactiva por defecto; ?permanent=true elimina de forma irreversible."""
        instance = self.get_object()

        if self._is_self_target(instance):
            return Response(
                {"detail": "No puede desactivar o eliminarse a sí mismo."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if request.query_params.get("permanent", "").lower() == "true":
            instance.delete()
        else:
            instance.is_active = False
            instance.save(update_fields=["is_active"])
        return Response(status=204)

    def update(self, request, *args, **kwargs):
        """Actualiza un usuario. El admin no puede alterarse a sí mismo en rol/estado."""
        instance = self.get_object()
        rejection = self._reject_self_admin_mutation(instance, request.data)
        if rejection is not None:
            return rejection
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        rejection = self._reject_self_admin_mutation(instance, request.data)
        if rejection is not None:
            return rejection
        return super().partial_update(request, *args, **kwargs)

