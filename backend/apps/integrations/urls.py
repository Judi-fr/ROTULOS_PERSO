"""ABM de integraciones (panel admin, ``integrations.manage``). Se monta
bajo ``/api/v1/integrations/`` (ver config/urls.py): ``/keys/``,
``/incoming-webhooks/``, ``/webhook-endpoints/``, ``/webhook-deliveries/``.
"""

from rest_framework.routers import SimpleRouter

from .views import (
    IncomingWebhookViewSet,
    IntegrationKeyViewSet,
    WebhookDeliveryListView,
    WebhookEndpointViewSet,
)
from django.urls import path

router = SimpleRouter()
router.register(r"keys", IntegrationKeyViewSet, basename="integration-key")
router.register(r"incoming-webhooks", IncomingWebhookViewSet, basename="incoming-webhook")
router.register(r"webhook-endpoints", WebhookEndpointViewSet, basename="webhook-endpoint")

urlpatterns = [
    path("webhook-deliveries/", WebhookDeliveryListView.as_view(), name="webhook-delivery-list"),
] + router.urls
