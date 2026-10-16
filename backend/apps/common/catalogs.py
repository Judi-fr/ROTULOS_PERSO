"""Lo común a los catálogos globales que administra el admin
(``catalogs.manage``): ``apps.carriers`` (transportistas manuales) y
``apps.labels`` (tamaños de rótulo). Cualquier usuario los LEE (son las
opciones que se le ofrecen); solo el admin los escribe. Borrar desactiva: lo
que ya se usó sigue nombrándolos."""

from rest_framework import viewsets
from rest_framework.permissions import SAFE_METHODS, IsAuthenticated

from apps.accounts.permissions_map import user_has_permission
from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record

MANAGE_PERMISSION = "catalogs.manage"


class CatalogViewSet(viewsets.ModelViewSet):
    """``?include_inactive=1`` (solo con ``catalogs.manage``) suma las
    desactivadas, para poder reactivarlas desde ``catalogos.html``."""

    http_method_names = ["get", "post", "patch", "delete"]
    pagination_class = None
    catalog_name = ""

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated()]
        return [IsAuthenticated(), HasRolePermission(MANAGE_PERMISSION)]

    def get_queryset(self):
        queryset = self.serializer_class.Meta.model.objects.all()
        wants_all = self.request.query_params.get("include_inactive") in ("1", "true")
        if wants_all and user_has_permission(self.request.user, MANAGE_PERMISSION):
            return queryset
        if self.action == "list" or not user_has_permission(self.request.user, MANAGE_PERMISSION):
            return queryset.filter(is_active=True)
        return queryset

    def _audit(self, instance, verb):
        record(
            self.request,
            category="catalogs",
            action="catalog.update",
            target=instance,
            target_type=instance._meta.model_name,
            target_repr=f"{self.catalog_name}: {instance} ({verb})",
        )

    def perform_create(self, serializer):
        self._audit(serializer.save(), "agregado")

    def perform_update(self, serializer):
        self._audit(serializer.save(), "editado")

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        self._audit(instance, "desactivado")
