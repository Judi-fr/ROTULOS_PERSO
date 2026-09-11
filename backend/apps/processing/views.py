"""Vistas de la app processing (lectura de rótulos con el modelo de Claude)."""

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from apps.accounts.role_permissions import HasRolePermission

from . import agente
from .models import EstadoImportacion, ImportacionRotulo
from .serializers import ImportacionRotuloSerializer


class ImportacionThrottle(UserRateThrottle):
    """Límite propio para las lecturas.

    Cada importación es una llamada al modelo que se paga por token, así que el
    límite general de 1000/min no sirve acá.
    """

    scope = "importacion_rotulo"


class ImportacionRotuloViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Lectura de un rótulo desde una foto o un PDF ya subido.

    ``POST`` con ``{"documento": <id>}`` procesa el archivo y devuelve la
    plantilla **propuesta**. No la guarda: el usuario la revisa en el editor y
    después la manda él mismo a ``POST /api/v1/labels/plantillas/``.

    No hay ``update`` ni ``destroy``: una lectura es un hecho registrado, no
    algo que se edite.
    """

    serializer_class = ImportacionRotuloSerializer
    throttle_classes = [ImportacionThrottle]

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("processing.import")]

    def get_queryset(self):
        return ImportacionRotulo.objects.filter(
            creada_por=self.request.user
        ).select_related("documento")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        importacion = serializer.save(creada_por=request.user)

        agente.interpretar(importacion)

        salida = self.get_serializer(importacion)
        codigo = (
            status.HTTP_201_CREATED
            if importacion.estado == EstadoImportacion.COMPLETADA
            else status.HTTP_502_BAD_GATEWAY
        )
        return Response(salida.data, status=codigo)

    @action(detail=True, methods=["post"])
    def reintentar(self, request, pk=None):
        """Vuelve a procesar el mismo documento en una importación nueva."""
        original = self.get_object()
        nueva = ImportacionRotulo.objects.create(
            documento=original.documento, creada_por=request.user
        )
        agente.interpretar(nueva)

        salida = self.get_serializer(nueva)
        codigo = (
            status.HTTP_201_CREATED
            if nueva.estado == EstadoImportacion.COMPLETADA
            else status.HTTP_502_BAD_GATEWAY
        )
        return Response(salida.data, status=codigo)
