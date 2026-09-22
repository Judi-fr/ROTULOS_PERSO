"""Vistas de la app processing (lectura de rótulos con el modelo de Claude)."""

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record

from . import agent
from .models import LabelImport, LabelImportStatus
from .serializers import LabelImportSerializer


class LabelImportThrottle(UserRateThrottle):
    """Límite propio para las lecturas.

    Cada importación es una llamada al modelo que se paga por token, así que el
    límite general de 1000/min no sirve acá.
    """

    scope = "importacion_rotulo"


class LabelImportViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Lectura de un rótulo desde una foto o un PDF ya subido.

    ``POST`` con ``{"uploaded_file": <id>}`` procesa el archivo y devuelve la
    plantilla **propuesta**. No la guarda: el usuario la revisa en el editor y
    después la manda él mismo a ``POST /api/v1/labels/element-layouts/``.

    No hay ``update`` ni ``destroy``: una lectura es un hecho registrado, no
    algo que se edite.
    """

    serializer_class = LabelImportSerializer
    throttle_classes = [LabelImportThrottle]

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("processing.import")]

    def get_queryset(self):
        return LabelImport.objects.filter(
            created_by=self.request.user
        ).select_related("uploaded_file")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        label_import = serializer.save(created_by=request.user)

        agent.process(label_import)

        record(
            request,
            category="processing",
            action="importacion_rotulo.create",
            target=label_import,
            target_type="importacionrotulo",
            target_repr=str(label_import),
            changes={"documento": {"from": None, "to": label_import.uploaded_file_id}},
        )

        output = self.get_serializer(label_import)
        code = (
            status.HTTP_201_CREATED
            if label_import.status == LabelImportStatus.COMPLETED
            else status.HTTP_502_BAD_GATEWAY
        )
        return Response(output.data, status=code)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        """Vuelve a procesar el mismo documento en una importación nueva."""
        original = self.get_object()
        new_import = LabelImport.objects.create(
            uploaded_file=original.uploaded_file, created_by=request.user
        )
        agent.process(new_import)

        record(
            request,
            category="processing",
            action="importacion_rotulo.create",
            target=new_import,
            target_type="importacionrotulo",
            target_repr=str(new_import),
            changes={
                "documento": {"from": None, "to": new_import.uploaded_file_id},
                "reintento_de": {"from": None, "to": original.pk},
            },
        )

        output = self.get_serializer(new_import)
        code = (
            status.HTTP_201_CREATED
            if new_import.status == LabelImportStatus.COMPLETED
            else status.HTTP_502_BAD_GATEWAY
        )
        return Response(output.data, status=code)
