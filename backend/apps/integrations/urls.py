"""Integraciones. Se monta bajo ``/api/v1/integrations/`` (ver config/urls.py).

- ABM admin (``integrations.manage``): ``/keys/``, ``/incoming-webhooks/``,
  ``/webhook-endpoints/``, ``/webhook-deliveries/``.
- Las vistas propias de cada plataforma están en su carpeta
  (``providers/<plataforma>/views.py``); acá solo se montan.
- Tiendas online del comerciante: ``/stores/`` (+ ``claim/``,
  ``<id>/disconnect/``) y, por cada plataforma registrada en
  ``providers``, ``/<plataforma>/install-url/``, ``/<plataforma>/callback/``
  (la redirect URL que se configura en su panel) y
  ``/<plataforma>/webhooks/``. Tiendanube suma ``/tiendanube/install-share-link/``
  + ``/tiendanube/install/<token>/`` (link de instalación para compartir con
  quien administra la tienda), y ``/tiendanube/print-link/`` +
  ``/tiendanube/print/<token>`` ("Imprimir rótulos" en las acciones masivas de
  Ventas, un link de app que abre ``imprimir_tiendanube.html``). Shopify suma ``/shopify/launch/`` (su App URL),
  ``/shopify/connect-manual/`` (conexión con la app propia del comerciante) y
  ``/shopify/print-link/`` + ``/shopify/print/<token>`` (rótulos impresos
  desde su admin, ver ``providers/shopify/admin_print.py``). WooCommerce no tiene ``callback/``:
  ``/woocommerce/keys/`` (las claves que POSTea su autorización),
  ``/woocommerce/return/`` (la vuelta del navegador) y
  ``/woocommerce/connect-manual/`` (claves pegadas a mano), más
  ``/woocommerce/print-link/`` + ``/woocommerce/print/<token>`` (rótulos
  impresos desde su admin con nuestro plugin, ver ``providers/woocommerce/admin_print.py``) y
  ``/woocommerce/print-plugin/`` (el plugin como zip) y ``/woocommerce/rates/``
  (la cotización del envío en su checkout, ver ``providers/woocommerce/rates.py``).
  VTEX no tiene ``install-url/`` ni ``callback/``: ``/vtex/connect-manual/``
  (el appKey/appToken que pega el comerciante) y ``/vtex/webhooks/`` (su hook
  de pedidos). Magento tampoco: ``/magento/connect-manual/`` (las credenciales de
  una Integración de su admin); sin webhooks en la fase 1. Empretienda no tiene
  API: ``/empretienda/connect/`` (la tienda, sin credenciales) y
  ``/empretienda/import/preview/`` + ``confirm/`` (su planilla de ventas).
- ``/store-labels/``: solo lectura, los rótulos que las tiendas del
  comerciante pidieron desde su propio admin (ver
  ``providers/tiendanube/labels.py``). Los endpoints que llama la plataforma
  viven aparte, en ``providers/tiendanube/label_urls.py``.
- ``/shipping-rates/``: ABM de la tabla de tarifas con la que cotizamos el
  envío en el checkout de esas tiendas (ver ``shipping_rates``). El
  callback que consulta esa tabla vive en ``providers/tiendanube/rate_urls.py``.
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from .providers import all_providers
from .providers.empretienda.views import (
    EmpretiendaConnectView,
    EmpretiendaImportConfirmView,
    EmpretiendaImportPreviewView,
)
from .providers.magento.views import MagentoManualConnectView
from .providers.shopify.views import (
    ShopifyLaunchView,
    ShopifyManualConnectView,
    ShopifyPrintLinkView,
    shopify_print_document,
)
from .providers.tiendanube.views import TiendanubePrintLinkView, tiendanube_print_document
from .providers.vtex.views import VtexManualConnectView
from .providers.woocommerce.views import (
    WooCommerceKeysView,
    WooCommerceManualConnectView,
    WooCommercePluginDownloadView,
    WooCommercePrintLinkView,
    WooCommerceRatesView,
    WooCommerceReturnView,
    woocommerce_print_document,
)
from .views import (
    IncomingWebhookViewSet,
    ShippingRateViewSet,
    IntegrationKeyViewSet,
    StoreConnectionViewSet,
    StoreInstallShareLinkView,
    StoreInstallShareView,
    StoreInstallUrlView,
    StoreLabelRequestViewSet,
    StoreOAuthCallbackView,
    StoreWebhookView,
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
    platform_urls.append(
        path(f"{_platform}/webhooks/", StoreWebhookView.as_view(), _kwargs, name=f"{_platform}-webhooks")
    )
    # VTEX no tiene a dónde mandar al comerciante a autorizar: pega su clave.
    if _provider.uses_install_url:
        platform_urls.append(
            path(f"{_platform}/install-url/", StoreInstallUrlView.as_view(), _kwargs, name=f"{_platform}-install-url")
        )
    # WooCommerce no vuelve con un code por GET: tiene sus propias rutas, abajo.
    if _provider.uses_authorization_code:
        platform_urls.append(
            path(f"{_platform}/callback/", StoreOAuthCallbackView.as_view(), _kwargs, name=f"{_platform}-callback")
        )
    # Link de instalación para compartir: solo donde se autoriza sin saber
    # antes de qué tienda se trata (Tiendanube).
    if _provider.uses_authorization_code and not _provider.requires_shop_domain:
        platform_urls += [
            path(
                f"{_platform}/install-share-link/",
                StoreInstallShareLinkView.as_view(),
                _kwargs,
                name=f"{_platform}-install-share-link",
            ),
            path(
                f"{_platform}/install/<str:token>/",
                StoreInstallShareView.as_view(),
                _kwargs,
                name=f"{_platform}-install-share",
            ),
        ]

urlpatterns = (
    [
        path("webhook-deliveries/", WebhookDeliveryListView.as_view(), name="webhook-delivery-list"),
        path("shopify/launch/", ShopifyLaunchView.as_view(), name="shopify-launch"),
        path("shopify/connect-manual/", ShopifyManualConnectView.as_view(), name="shopify-connect-manual"),
        path("shopify/print-link/", ShopifyPrintLinkView.as_view(), name="shopify-print-link"),
        path("shopify/print/<str:token>", shopify_print_document, name="shopify-print"),
        path("tiendanube/print-link/", TiendanubePrintLinkView.as_view(), name="tiendanube-print-link"),
        path("tiendanube/print/<str:token>", tiendanube_print_document, name="tiendanube-print"),
        path("woocommerce/keys/", WooCommerceKeysView.as_view(), name="woocommerce-keys"),
        path("woocommerce/return/", WooCommerceReturnView.as_view(), name="woocommerce-return"),
        path("woocommerce/connect-manual/", WooCommerceManualConnectView.as_view(), name="woocommerce-connect-manual"),
        path("woocommerce/print-plugin/", WooCommercePluginDownloadView.as_view(), name="woocommerce-print-plugin"),
        path("woocommerce/rates/", WooCommerceRatesView.as_view(), name="woocommerce-rates"),
        path("woocommerce/print-link/", WooCommercePrintLinkView.as_view(), name="woocommerce-print-link"),
        path("woocommerce/print/<str:token>", woocommerce_print_document, name="woocommerce-print"),
        path("vtex/connect-manual/", VtexManualConnectView.as_view(), name="vtex-connect-manual"),
        path("magento/connect-manual/", MagentoManualConnectView.as_view(), name="magento-connect-manual"),
        path("empretienda/connect/", EmpretiendaConnectView.as_view(), name="empretienda-connect"),
        path("empretienda/import/preview/", EmpretiendaImportPreviewView.as_view(), name="empretienda-import-preview"),
        path("empretienda/import/confirm/", EmpretiendaImportConfirmView.as_view(), name="empretienda-import-confirm"),
    ]
    + platform_urls
    + router.urls
)
