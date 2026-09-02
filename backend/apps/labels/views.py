"""Vistas de la app labels (catálogo de variables y plantillas de rótulos)."""

from django.db.models import Prefetch, ProtectedError
from rest_framework import status, viewsets
from rest_framework.response import Response

from .models import ElementoPlantilla, Plantilla, VariableRotulo
from .permissions import LecturaAutenticadaEscrituraAdministrador
from .serializers import PlantillaSerializer, VariableRotuloSerializer


class VariableRotuloViewSet(viewsets.ModelViewSet):
    """CRUD del catálogo de variables que puede contener un rótulo.

    Leer el catálogo lo puede hacer cualquier usuario autenticado —el editor
    lo necesita para armar sus selectores—; modificarlo queda reservado a
    administradores, porque es un recurso compartido por todas las plantillas.

    El listado devuelve solo las variables activas, que es lo que quiere la
    interfaz. Con ``?incluir_inactivas=1`` se obtienen todas, para la pantalla
    de administración del catálogo.

    Sin paginación a propósito: es un catálogo de pocas decenas de filas que se
    consume entero para poblar un selector; paginarlo solo complicaría al
    cliente.
    """

    serializer_class = VariableRotuloSerializer
    permission_classes = [LecturaAutenticadaEscrituraAdministrador]
    pagination_class = None

    def get_queryset(self):
        # select_related: el serializer expone el email de quien creó la
        # variable, que vive en otra tabla.
        queryset = VariableRotulo.objects.select_related("creada_por")
        if self.action == "list":
            incluir = self.request.query_params.get("incluir_inactivas")
            if incluir not in ("1", "true", "True"):
                queryset = queryset.filter(activa=True)
        return queryset

    def perform_create(self, serializer):
        serializer.save(creada_por=self.request.user)

    def destroy(self, request, *args, **kwargs):
        """Elimina una variable, salvo que sea del sistema o esté en uso.

        Para retirar de circulación una variable que ya se usó, el camino es
        marcarla como inactiva: así deja de ofrecerse sin romper las plantillas
        que la tienen colocada.
        """
        variable = self.get_object()

        if variable.es_sistema:
            return Response(
                {
                    "detail": "Las variables del sistema no se pueden eliminar. "
                    "Si no se usa, marcala como inactiva."
                },
                status=status.HTTP_409_CONFLICT,
            )

        try:
            variable.delete()
        except ProtectedError:
            return Response(
                {
                    "detail": "La variable está en uso en alguna plantilla. "
                    "Marcala como inactiva en lugar de eliminarla."
                },
                status=status.HTTP_409_CONFLICT,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)


class PlantillaViewSet(viewsets.ModelViewSet):
    """CRUD de plantillas de rótulos.

    Requiere usuario autenticado (política por defecto del proyecto). Al crear,
    ``creada_por`` se fija con el usuario de la petición para dejar trazabilidad
    sin confiar en el cuerpo.

    Las dos optimizaciones del queryset evitan sendos N+1 al serializar el
    listado: el ``Prefetch`` trae los elementos con la variable de cada uno
    (que aporta la etiqueta legible), y el ``select_related`` trae al usuario
    que creó la plantilla (del que se expone el email).
    """

    queryset = (
        Plantilla.objects.select_related("creada_por")
        .prefetch_related(
            Prefetch(
                "elementos",
                queryset=ElementoPlantilla.objects.select_related("variable"),
            )
        )
    )
    serializer_class = PlantillaSerializer

    def perform_create(self, serializer):
        serializer.save(creada_por=self.request.user)
