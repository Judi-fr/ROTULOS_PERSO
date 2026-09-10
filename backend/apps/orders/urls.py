"""Rutas de direcciones y pedidos propios, más el listado admin de pedidos
y la carga operativa (alta manual, importación) de las stories 20-22.

Se montan bajo ``/api/v1/`` (ver config/urls.py), igual que
``management_urls.py``, dando URLs planas: ``/api/v1/addresses/`` y
``/api/v1/orders/``. SimpleRouter porque la API es JSON pura.
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from .import_views import (
    ImportMappingViewSet,
    OrderImportConfirmView,
    OrderImportTemplateView,
    OrderImportUploadView,
    OrderImportValidateView,
)
from .views import AddressViewSet, AdminOrderListView, ManualOrderCreateView, OrderMetricsView, OrderViewSet

router = SimpleRouter()
router.register(r"addresses", AddressViewSet, basename="address")
router.register(r"orders", OrderViewSet, basename="order")
router.register(r"orders/import-mappings", ImportMappingViewSet, basename="import-mapping")

urlpatterns = [
    # Pedidos de TODOS los usuarios (panel admin, orders.view_all). Paths
    # explícitos (antes del router) para no chocar con /orders/<pk>/ del
    # self-service: Django resuelve urlpatterns en orden y estos van primero.
    path("admin/orders/", AdminOrderListView.as_view(), name="admin-order-list"),
    path("orders/metrics/", OrderMetricsView.as_view(), name="order-metrics"),
    # Carga operativa (story 20): alta manual de un envío a nombre propio o
    # (con orders.create_for_others) de otro usuario.
    path("orders/manual/", ManualOrderCreateView.as_view(), name="order-manual-create"),
    # Importación CSV/Excel (story 21), en tres pasos: subir -> mapear ->
    # confirmar. La plantilla descargable (story 22) va antes del router
    # para no chocar con /orders/imports/<pk>/.
    path("orders/imports/template/", OrderImportTemplateView.as_view(), name="order-import-template"),
    path("orders/imports/", OrderImportUploadView.as_view(), name="order-import-upload"),
    path(
        "orders/imports/<int:pk>/validate/",
        OrderImportValidateView.as_view(),
        name="order-import-validate",
    ),
    path(
        "orders/imports/<int:pk>/confirm/",
        OrderImportConfirmView.as_view(),
        name="order-import-confirm",
    ),
] + router.urls
