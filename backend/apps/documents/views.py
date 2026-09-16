"""Vistas de la app documents (subida de fotos y PDF de rótulos)."""

from rest_framework import mixins, viewsets

from .models import Documento
from .serializers import DocumentoSerializer


class DocumentoViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Subida y consulta de archivos de rótulo.

    No hay ``update``: un archivo subido no se edita. Si el usuario quiere otro,
    sube uno nuevo — así el documento sigue siendo un registro fiel de lo que se
    le mandó al modelo en cada importación.

    Cada usuario ve únicamente sus propios documentos. Son fotos que sacó de
    rótulos de sus clientes y no hay motivo para que sean visibles entre
    cuentas; el catálogo de variables es lo único compartido en el sistema.
    """

    serializer_class = DocumentoSerializer

    def get_queryset(self):
        return Documento.objects.filter(subido_por=self.request.user)

    def perform_create(self, serializer):
        serializer.save(subido_por=self.request.user)
