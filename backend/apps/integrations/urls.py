"""Integraciones. Se monta bajo ``/api/v1/integrations/`` (ver config/urls.py).

- ABM admin (``integrations.manage``): ``/keys/``, ``/incoming-webhooks/``,
  ``/webhook-endpoints/``, ``/webhook-deliveries/``.
- Tiendas online del comerciante: ``/stores/`` (+ ``claim/``,
  ``<id>/disconnect/``), ``/tiendanube/install-url/`` y
  ``/tiendanube/callback/`` (la redirect URL del Portal de Partners).
- ``/store-labels/``: solo lectura, los rótulos que las tiendas del
  comerciante pidieron desde su propio admin (ver ``store_labels``). Los
  endpoints que llama la plataforma viven aparte, en ``label_urls``.
- ``/shipping-rates/``: ABM de la tabla de tarifas con la que cotizamos el
  envío en el checkout de esas tiendas (ver ``shipping_rates``). El
  callback que consulta esa tabla vive en ``rate_urls``.
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    IncomingWebhookViewSet,
    ShippingRateViewSet,
    IntegrationKeyViewSet,
    StoreConnectionViewSet,
    StoreLabelRequestViewSet,
    TiendanubeCallbackView,
    TiendanubeInstallUrlView,
    TiendanubeWebhookView,
    WebhookDeliveryListView,
    WebhookEndpointViewSet,
)

router = SimpleRouter()
router.register(r"keys", IntegrationKeyViewSet, basename="integration-key")
router.register(r"incoming-webhooks", IncomingWebhookViewSet, basename="incoming-webhook")
router.register(r"webhook-endpoints", WebhookEndpointViewSet, basename="webhook-endpoint")
router.register(r"stores", StoreConnectionViewSet, basename="store-connection")
router.register(r"store-labels", StoreLabelRequestViewSet, basename="store-label-request")
router.register(r"shipping-rates", ShippingRateViewSet, basename="shipping-rate")

urlpatterns = [
    path("webhook-deliveries/", WebhookDeliveryListView.as_view(), name="webhook-delivery-list"),
    path("tiendanube/install-url/", TiendanubeInstallUrlView.as_view(), name="tiendanube-install-url"),
    path("tiendanube/callback/", TiendanubeCallbackView.as_view(), name="tiendanube-callback"),
    path("tiendanube/webhooks/", TiendanubeWebhookView.as_view(), name="tiendanube-webhooks"),
] + router.urls
