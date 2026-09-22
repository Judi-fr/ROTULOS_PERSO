"""Rutas de la app processing (agente que lee rótulos desde una imagen).

Se montan bajo ``/api/v1/processing/``:

    POST /api/v1/processing/label-imports/              -> procesar un documento
    GET  /api/v1/processing/label-imports/               -> listar las propias
    GET  /api/v1/processing/label-imports/<id>/          -> detalle
    POST /api/v1/processing/label-imports/<id>/retry/    -> volver a leer

El flujo completo de importar un rótulo son tres pasos:

    1. POST /api/v1/documents/documentos/        (sube la foto)
    2. POST /api/v1/processing/label-imports/     (la lee -> propuesta)
    3. POST /api/v1/labels/element-layouts/       (el usuario guarda lo revisado)
"""

from rest_framework.routers import SimpleRouter

from .views import LabelImportViewSet

router = SimpleRouter()
router.register(
    r"label-imports", LabelImportViewSet, basename="label-import"
)

urlpatterns = router.urls
