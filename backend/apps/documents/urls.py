"""Rutas de la app documents (carga de imágenes / PDF de rótulos).

Se montan bajo ``/api/v1/documents/`` (ver config/urls.py):

    GET    /api/v1/documents/documentos/       -> listar los propios (paginado)
    POST   /api/v1/documents/documentos/       -> subir (multipart/form-data)
    GET    /api/v1/documents/documentos/<id>/  -> detalle
    DELETE /api/v1/documents/documentos/<id>/  -> eliminar

Se usa SimpleRouter (no DefaultRouter) porque la API es JSON pura, igual que
en el resto del proyecto.
"""

from rest_framework.routers import SimpleRouter

from .views import DocumentoViewSet

router = SimpleRouter()
router.register(r"documentos", DocumentoViewSet, basename="documento")

urlpatterns = router.urls
