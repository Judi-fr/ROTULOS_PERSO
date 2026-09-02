"""Rutas administrativas adicionales montadas en ``/api/v1/`` (fuera de
``/auth/``, igual que ``management_urls.py``): hoy solo la bandeja de
soporte del admin."""

from rest_framework.routers import SimpleRouter

from .support_views import AdminSupportMessageViewSet

router = SimpleRouter()
router.register(r"support-messages", AdminSupportMessageViewSet, basename="admin-support-message")

urlpatterns = router.urls
