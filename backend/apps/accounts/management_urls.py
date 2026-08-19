"""Rutas de la administración de usuarios (CRUD del admin).

Se montan bajo ``/api/v1/users/`` (ver config/urls.py). Se usa SimpleRouter
(y no DefaultRouter) porque la API es JSON pura y no necesita la vista raíz
navegable que agrega DefaultRouter.
"""

from rest_framework.routers import SimpleRouter

from .management_views import UserAdminViewSet

router = SimpleRouter()
router.register(r"users", UserAdminViewSet, basename="user")

urlpatterns = router.urls
