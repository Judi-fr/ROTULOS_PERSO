"""Rutas de rótulos y plantillas. Se montan bajo ``/api/v1/labels/``
(ver config/urls.py), dando URLs como ``/api/v1/labels/labels/`` y
``/api/v1/labels/templates/``. SimpleRouter porque la API es JSON pura,
mismo patrón que ``apps.orders.urls``.
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from .batch_views import LabelBatchView
from .views import (
    AdminLabelListView,
    BarcodeImageView,
    FuentesView,
    LabelTemplateViewSet,
    LabelViewSet,
    PlantillaViewSet,
    RenderLabelView,
    VariableRotuloViewSet,
)

router = SimpleRouter()
router.register(r"labels", LabelViewSet, basename="label")
router.register(r"templates", LabelTemplateViewSet, basename="label-template")
router.register(r"plantillas", PlantillaViewSet, basename="plantilla")
router.register(r"variables", VariableRotuloViewSet, basename="variable-rotulo")

urlpatterns = [
    # Rótulos de TODOS los usuarios (panel admin, labels.view_all). Path
    # explícito (antes del router) por consistencia con admin/orders/,
    # aunque acá no choca con ningún path del router.
    path("admin/", AdminLabelListView.as_view(), name="admin-label-list"),
    # Render sin persistir (plantilla + pedido -> PDF), semilla del lote.
    path("render/", RenderLabelView.as_view(), name="label-render"),
    # SVG suelto de un QR/código de barras (preview del editor + debug).
    path("barcode/", BarcodeImageView.as_view(), name="label-barcode"),
    # Generación por lote: muchos rótulos -> un Document (apps.documents).
    path("batch/", LabelBatchView.as_view(), name="label-batch"),
    # Familias tipográficas para el editor de plantillas nuevas (no es un
    # recurso con CRUD, va como vista suelta).
    path("fuentes/", FuentesView.as_view(), name="fuentes"),
] + router.urls
