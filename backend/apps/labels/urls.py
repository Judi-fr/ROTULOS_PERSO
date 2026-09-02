"""Rutas de la app labels (rótulos, plantillas e impresión).

Se montan bajo ``/api/v1/labels/`` (ver config/urls.py):

    GET    /api/v1/labels/plantillas/            -> listar (paginado)
    POST   /api/v1/labels/plantillas/            -> crear
    GET    /api/v1/labels/plantillas/<id>/       -> detalle
    PATCH  /api/v1/labels/plantillas/<id>/       -> editar (parcial)
    PUT    /api/v1/labels/plantillas/<id>/       -> editar (completo)
    DELETE /api/v1/labels/plantillas/<id>/       -> eliminar

    GET    /api/v1/labels/variables/             -> catálogo (activas, sin paginar)
    POST   /api/v1/labels/variables/             -> crear      (administradores)
    GET    /api/v1/labels/variables/<id>/        -> detalle
    PATCH  /api/v1/labels/variables/<id>/        -> editar     (administradores)
    DELETE /api/v1/labels/variables/<id>/        -> eliminar   (administradores)

Se usa SimpleRouter (no DefaultRouter) porque la API es JSON pura, igual que
en la administración de usuarios.
"""

from rest_framework.routers import SimpleRouter

from .views import PlantillaViewSet, VariableRotuloViewSet

router = SimpleRouter()
router.register(r"plantillas", PlantillaViewSet, basename="plantilla")
router.register(r"variables", VariableRotuloViewSet, basename="variable-rotulo")

urlpatterns = router.urls
