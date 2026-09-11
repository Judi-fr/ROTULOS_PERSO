"""Rutas de documentos generados por el sistema. Se montan bajo
``/api/v1/documents/`` (ver config/urls.py). Router con prefijo vacío:
``GET/DELETE /api/v1/documents/<id>/`` y ``GET /api/v1/documents/`` tal
cual pide el endpoint, más la acción ``download`` anidada del router
(``/api/v1/documents/<id>/download/``).
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import AdminDocumentListView, DocumentoViewSet, DocumentViewSet

router = SimpleRouter()
# ``documentos`` se registra PRIMERO que el router con prefijo "" del
# ``DocumentViewSet``: el patrón ``<pk>/`` del segundo matchearía la palabra
# ``documentos`` como un id (el mismo problema que se resuelve para
# ``admin/`` con un path explícito ANTES del router).
router.register(r"documentos", DocumentoViewSet, basename="documento")
router.register(r"", DocumentViewSet, basename="document")

urlpatterns = [
    # Documentos de TODOS los usuarios (panel admin, documents.view_all).
    # Path explícito ANTES del router: con prefijo "" el router también
    # generaría un patrón `<pk>/` que matchearía "admin" como id si este
    # path no fuera primero (Django resuelve urlpatterns en orden).
    path("admin/", AdminDocumentListView.as_view(), name="admin-document-list"),
] + router.urls
