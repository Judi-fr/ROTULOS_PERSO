"""Rutas de documentos generados por el sistema. Se montan bajo
``/api/v1/documents/`` (ver config/urls.py). Router con prefijo vacío:
``GET/DELETE /api/v1/documents/<id>/`` y ``GET /api/v1/documents/`` tal
cual pide el endpoint, más la acción ``download`` anidada del router
(``/api/v1/documents/<id>/download/``).
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import AdminDocumentListView, DocumentViewSet

router = SimpleRouter()
router.register(r"", DocumentViewSet, basename="document")

urlpatterns = [
    # Documentos de TODOS los usuarios (panel admin, documents.view_all).
    # Path explícito ANTES del router: con prefijo "" el router también
    # generaría un patrón `<pk>/` que matchearía "admin" como id si este
    # path no fuera primero (Django resuelve urlpatterns en orden).
    path("admin/", AdminDocumentListView.as_view(), name="admin-document-list"),
] + router.urls
