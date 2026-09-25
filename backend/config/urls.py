"""Rutas raíz del proyecto. La API se versiona bajo /api/v1/."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from .health import LivenessView, ReadinessView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Dos sondas, con jobs distintos: liveness no toca nada y readiness
    # consulta la base (ver config/health.py).
    path("api/v1/health/", LivenessView.as_view(), name="health"),
    path("api/v1/health/ready/", ReadinessView.as_view(), name="health-ready"),
    path("api/v1/auth/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.accounts.user_admin_urls")),  # CRUD de usuarios (admin)
    path("api/v1/", include("apps.accounts.support_admin_urls")),  # bandeja de soporte (admin)
    path("api/v1/", include("apps.orders.urls")),  # direcciones y pedidos propios
    path("api/v1/audit/", include("apps.audit.urls")),  # registro de auditoría (solo lectura)
    path("api/v1/documents/", include("apps.documents.urls")),  # documentos generados (lotes de rótulos)
    path("api/v1/processing/", include("apps.processing.urls")),
    path("api/v1/labels/", include("apps.labels.urls")),
    path("api/v1/integrations/", include("apps.integrations.urls")),  # ABM de claves/webhooks (admin)
    path("api/v1/integrations/", include("apps.integrations.label_urls")),  # rótulos pedidos por la tienda
    path("api/v1/integrations/", include("apps.integrations.rate_urls")),  # cotización de envíos en el checkout
    path("api/v1/ingest/", include("apps.integrations.ingest_urls")),  # entradas automáticas (API key / webhook)
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
