"""Integraciones. Se monta bajo ``/api/v1/integrations/`` (ver config/urls.py).

- ABM admin (``integrations.manage``): ``/keys/``, ``/incoming-webhooks/``,
  ``/webhook-endpoints/``, ``/webhook-deliveries/``.
- Tiendas online del comerciante: ``/stores/`` (+ ``claim/``,
  ``<id>/disconnect/``) y, por cada plataforma registrada en
  ``providers``, ``/<plataforma>/install-url/``, ``/<plataforma>/callback/``
  (la redirect URL que se configura en su panel) y
  ``/<plataforma>/webhooks/``. Shopify suma ``/shopify/launch/`` (su App URL),
  ``/shopify/connect-manual/`` (conexión con la app propia del comerciante) y
  ``/shopify/print-link/`` + ``/shopify/print/<token>`` (rótulos impresos
  desde su admin, ver ``shopify_print``). WooCommerce no tiene ``callback/``:
  ``/woocommerce/keys/`` (las claves que POSTea su autorización),
  ``/woocommerce/return/`` (la vuelta del navegador) y
  ``/woocommerce/connect-manual/`` (claves pegadas a mano), más
  ``/woocommerce/print-link/`` + ``/woocommerce/print/<token>`` (rótulos
  impresos desde su admin con nuestro plugin, ver ``woocommerce_print``) y
  ``/woocommerce/print-plugin/`` (el plugin como zip) y ``/woocommerce/rates/``
  (la cotización del envío en su checkout, ver ``woocommerce_rates``).
- ``/store-labels/``: solo lectura, los rótulos que las tiendas del
  comerciante pidieron desde su propio admin (ver ``store_labels``). Los
  endpoints que llama la plataforma viven aparte, en ``label_urls``.
- ``/shipping-rates/``: ABM de la tabla de tarifas con la que cotizamos el
  envío en el checkout de esas tiendas (ver ``shipping_rates``). El
  callback que consulta esa tabla vive en ``rate_urls``.
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from .providers import all_providers
from .views import (
    IncomingWebhookViewSet,
    ShippingRateViewSet,
    IntegrationKeyViewSet,
    ShopifyLaunchView,
    ShopifyManualConnectView,
    ShopifyPrintLinkView,
    shopify_print_document,
    StoreConnectionViewSet,
    StoreInstallUrlView,
    StoreLabelRequestViewSet,
    StoreOAuthCallbackView,
    StoreWebhookView,
    WooCommerceKeysView,
    WooCommerceManualConnectView,
    WooCommercePluginDownloadView,
    WooCommercePrintLinkView,
    WooCommerceRatesView,
    woocommerce_print_document,
    WooCommerceReturnView,
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

# Una ruta fija por plataforma (no un <str:platform>): así una plataforma
# que no existe da 404 y los nombres (``tiendanube-webhooks``,
# ``shopify-callback``...) se pueden resolver con reverse().
platform_urls = []
for _provider in all_providers():
    _platform = _provider.platform
    _kwargs = {"platform": _platform}
    platform_urls += [
        path(f"{_platform}/install-url/", StoreInstallUrlView.as_view(), _kwargs, name=f"{_platform}-install-url"),
        path(f"{_platform}/webhooks/", StoreWebhookView.as_view(), _kwargs, name=f"{_platform}-webhooks"),
    ]
    # WooCommerce no vuelve con un code por GET: tiene sus propias rutas, abajo.
    if _provider.uses_authorization_code:
        platform_urls.append(
            path(f"{_platform}/callback/", StoreOAuthCallbackView.as_view(), _kwargs, name=f"{_platform}-callback")
        )

urlpatterns = (
    [
        path("webhook-deliveries/", WebhookDeliveryListView.as_view(), name="webhook-delivery-list"),
        path("shopify/launch/", ShopifyLaunchView.as_view(), name="shopify-launch"),
        path("shopify/connect-manual/", ShopifyManualConnectView.as_view(), name="shopify-connect-manual"),
        path("shopify/print-link/", ShopifyPrintLinkView.as_view(), name="shopify-print-link"),
        path("shopify/print/<str:token>", shopify_print_document, name="shopify-print"),
        path("woocommerce/keys/", WooCommerceKeysView.as_view(), name="woocommerce-keys"),
        path("woocommerce/return/", WooCommerceReturnView.as_view(), name="woocommerce-return"),
        path("woocommerce/connect-manual/", WooCommerceManualConnectView.as_view(), name="woocommerce-connect-manual"),
        path("woocommerce/print-plugin/", WooCommercePluginDownloadView.as_view(), name="woocommerce-print-plugin"),
        path("woocommerce/rates/", WooCommerceRatesView.as_view(), name="woocommerce-rates"),
        path("woocommerce/print-link/", WooCommercePrintLinkView.as_view(), name="woocommerce-print-link"),
        path("woocommerce/print/<str:token>", woocommerce_print_document, name="woocommerce-print"),
    ]
    + platform_urls
    + router.urls
)
