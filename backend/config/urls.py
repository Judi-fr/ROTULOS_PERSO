"""Rutas raíz del proyecto. La API se versiona bajo /api/v1/."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthCheckView(APIView):
    """Endpoint mínimo para verificar que la API responde."""

    # Público: se usa para health checks del balanceador/monitor, sin auth.
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "ok", "service": "rotulos-perso-api"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", HealthCheckView.as_view(), name="health"),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.accounts.management_urls")),  # CRUD de usuarios (admin)
    path("api/v1/documents/", include("apps.documents.urls")),
    path("api/v1/processing/", include("apps.processing.urls")),
    path("api/v1/labels/", include("apps.labels.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
