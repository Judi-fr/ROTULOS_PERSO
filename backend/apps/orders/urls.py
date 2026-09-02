"""Rutas de direcciones y pedidos propios, más el listado admin de pedidos.

Se montan bajo ``/api/v1/`` (ver config/urls.py), igual que
``management_urls.py``, dando URLs planas: ``/api/v1/addresses/`` y
``/api/v1/orders/``. SimpleRouter porque la API es JSON pura.
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import AddressViewSet, AdminOrderListView, OrderMetricsView, OrderViewSet

router = SimpleRouter()
router.register(r"addresses", AddressViewSet, basename="address")
router.register(r"orders", OrderViewSet, basename="order")

urlpatterns = [
    # Pedidos de TODOS los usuarios (panel admin, orders.view_all). Paths
    # explícitos (antes del router) para no chocar con /orders/<pk>/ del
    # self-service: Django resuelve urlpatterns en orden y estos van primero.
    path("admin/orders/", AdminOrderListView.as_view(), name="admin-order-list"),
    path("orders/metrics/", OrderMetricsView.as_view(), name="order-metrics"),
] + router.urls
