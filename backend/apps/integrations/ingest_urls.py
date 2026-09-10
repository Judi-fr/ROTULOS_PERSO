"""Entradas automáticas de pedidos (stories 23-24). Se monta bajo
``/api/v1/ingest/`` (ver config/urls.py): ``POST /orders/`` (API,
``Api-Key``) y ``POST /webhooks/<slug>/`` (webhook entrante, firma HMAC).
Deliberadamente SEPARADO de ``urls.py`` (el ABM admin, JWT normal): estas
dos vistas tienen su propia autenticación, no la global de la API.
"""

from django.urls import path

from .views import IncomingWebhookView, IngestOrderView

urlpatterns = [
    path("orders/", IngestOrderView.as_view(), name="ingest-order"),
    path("webhooks/<slug:slug>/", IncomingWebhookView.as_view(), name="incoming-webhook"),
]
