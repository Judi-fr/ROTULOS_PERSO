"""Entradas automáticas de pedidos (stories 23-24). Se monta bajo
``/api/v1/ingest/`` (ver config/urls.py): ``POST /orders/`` (API,
``Api-Key``) y ``POST /webhooks/<slug>/`` (webhook entrante, firma HMAC).
Deliberadamente SEPARADO de ``urls.py`` (el ABM admin, JWT normal): estas
dos vistas tienen su propia autenticación, no la global de la API.
"""

from django.http import JsonResponse
from django.urls import path
from django.views.decorators.http import require_GET

from .ingest_openapi import build_ingest_openapi
from .views import IncomingWebhookView, IngestOrderView


@require_GET
def ingest_openapi_view(request):
    """GET /api/v1/ingest/openapi.json — el contrato de esta API (público:
    describe, no expone datos). El servidor es el mismo host que lo sirve."""
    return JsonResponse(
        build_ingest_openapi(request.build_absolute_uri("/api/v1/ingest").rstrip("/")),
        json_dumps_params={"ensure_ascii": False, "indent": 2},
    )


urlpatterns = [
    path("openapi.json", ingest_openapi_view, name="ingest-openapi"),
    path("orders/", IngestOrderView.as_view(), name="ingest-order"),
    path("webhooks/<slug:slug>/", IncomingWebhookView.as_view(), name="incoming-webhook"),
]
