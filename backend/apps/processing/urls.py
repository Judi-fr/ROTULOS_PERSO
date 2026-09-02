"""Rutas de la app processing (agente que lee rótulos desde una imagen).

Se montan bajo ``/api/v1/processing/`` (ver config/urls.py):

    POST /api/v1/processing/importaciones/              -> procesar un documento
    GET  /api/v1/processing/importaciones/              -> listar las propias
    GET  /api/v1/processing/importaciones/<id>/         -> detalle
    POST /api/v1/processing/importaciones/<id>/reintentar/ -> volver a leer

El flujo completo de importar un rótulo son tres pasos, cada uno en su app:

    1. POST /api/v1/documents/documentos/       (sube la foto)
    2. POST /api/v1/processing/importaciones/   (la lee -> propuesta)
    3. POST /api/v1/labels/plantillas/          (el usuario guarda lo revisado)

El paso 3 lo hace el cliente con el contenido de ``propuesta``, sin la clave
``_revision``, que es información para la pantalla de revisión y no forma
parte del cuerpo que acepta la API de plantillas.
"""

from rest_framework.routers import SimpleRouter

from .views import ImportacionRotuloViewSet

router = SimpleRouter()
router.register(
    r"importaciones", ImportacionRotuloViewSet, basename="importacion-rotulo"
)

urlpatterns = router.urls
