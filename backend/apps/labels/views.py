"""Vistas de la app labels (catálogo de variables y plantillas de rótulos)."""

from django.db.models import Prefetch, ProtectedError
from django.http import HttpResponse
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from . import render
from .models import ElementoPlantilla, Plantilla, VariableRotulo
from .render.fuentes import FuenteNoDisponible, familias_disponibles
from .permissions import LecturaAutenticadaEscrituraAdministrador
from .serializers import (
    PlantillaSerializer,
    RenderizarSerializer,
    VariableRotuloSerializer,
)


class FuentesView(APIView):
    """Familias tipográficas que el editor puede ofrecer.

    Se consulta al servidor en vez de codificar la lista en el frontend porque
    la disponibilidad depende de la máquina: el PDF sale siempre —sus fuentes
    viajan dentro del formato— pero el PNG necesita un ``.ttf`` instalado, y
    eso cambia entre tu Windows y un contenedor. Cada familia viene con
    ``png_disponible`` para que la interfaz pueda avisar antes de que alguien
    elija una y no entienda por qué la vista previa se ve distinta.
    """

    def get(self, request):
        return Response(familias_disponibles())


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

    @action(detail=True, methods=["post"])
    def renderizar(self, request, pk=None):
        """Genera el rótulo imprimible y lo devuelve como archivo.

        Es el endpoint que convierte un diseño guardado en algo que se pega en
        un paquete. Tres formas de usarlo, todas sobre el mismo código:

        - ``{"datos": {...}}`` -> un rótulo con datos reales.
        - ``{}`` -> vista previa, con cada variable dibujada como su etiqueta.
        - ``{"lote": [{...}, {...}]}`` -> un PDF de varias páginas.

        Dos cabeceras de respuesta informan lo que pasó sin romper el flujo:
        ``X-Rotulo-Faltantes`` lista las variables que no vinieron en los
        datos, ``X-Rotulo-Truncados`` las que no entraban en su caja y se
        cortaron, y ``X-Rotulo-Avisos`` el resto —hoy, un QR que quedó
        demasiado denso para el tamaño de su caja y que ningún lector va a
        levantar—. Recortar un domicilio o imprimir un QR ilegible en silencio
        es la clase de error que termina en un paquete que no llega, así que
        el dato viaja aunque la impresión siga adelante.
        """
        plantilla = self.get_object()
        entrada = RenderizarSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        opciones = entrada.validated_data

        try:
            if opciones["formato"] == "png":
                contenido, informe = render.renderizar_png(
                    plantilla,
                    datos=opciones.get("datos"),
                    usuario=request.user,
                    dpi=opciones.get("dpi"),
                )
                tipo, extension = "image/png", "png"
            else:
                contenido, informe = render.renderizar_pdf(
                    plantilla,
                    datos=opciones.get("datos"),
                    usuario=request.user,
                    lote=opciones.get("lote"),
                )
                tipo, extension = "application/pdf", "pdf"
        except FuenteNoDisponible as exc:
            # Es un problema de instalación del servidor, no de la petición.
            return Response({"detail": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)

        respuesta = HttpResponse(contenido, content_type=tipo)
        respuesta["Content-Disposition"] = (
            f'attachment; filename="rotulo-{plantilla.pk}.{extension}"'
        )
        if informe["faltantes"]:
            respuesta["X-Rotulo-Faltantes"] = ",".join(informe["faltantes"])
        if informe["truncados"]:
            respuesta["X-Rotulo-Truncados"] = ",".join(informe["truncados"])
        if informe["avisos"]:
            respuesta["X-Rotulo-Avisos"] = ",".join(informe["avisos"])
        return respuesta
