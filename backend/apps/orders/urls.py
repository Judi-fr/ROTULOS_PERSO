"""Rutas de direcciones y pedidos propios.

Se montan bajo ``/api/v1/`` (ver config/urls.py), igual que
``management_urls.py``, dando URLs planas: ``/api/v1/addresses/`` y
``/api/v1/orders/``. SimpleRouter porque la API es JSON pura.
"""

from rest_framework.routers import SimpleRouter

from .views import AddressViewSet, OrderViewSet

router = SimpleRouter()
router.register(r"addresses", AddressViewSet, basename="address")
router.register(r"orders", OrderViewSet, basename="order")

urlpatterns = router.urls
