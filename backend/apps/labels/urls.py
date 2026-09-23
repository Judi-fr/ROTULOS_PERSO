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
    AvailableFontsView,
    BarcodeImageView,
    ElementLayoutViewSet,
    LabelTemplateViewSet,
    LabelViewSet,
    LayoutVariableViewSet,
    PreviewLabelView,
    RenderLabelView,
)

router = SimpleRouter()
router.register(r"labels", LabelViewSet, basename="label")
router.register(r"templates", LabelTemplateViewSet, basename="label-template")
router.register(r"element-layouts", ElementLayoutViewSet, basename="element-layout")
router.register(r"layout-variables", LayoutVariableViewSet, basename="layout-variable")

urlpatterns = [
    # Rótulos de TODOS los usuarios (panel admin, labels.view_all). Path
    # explícito (antes del router) por consistencia con admin/orders/,
    # aunque acá no choca con ningún path del router.
    path("admin/", AdminLabelListView.as_view(), name="admin-label-list"),
    # Render sin persistir (plantilla + pedido -> PDF), semilla del lote.
    path("render/", RenderLabelView.as_view(), name="label-render"),
    # Vista previa de un diseño sin guardar, con datos de muestra: es lo que
    # hace que el editor muestre lo mismo que se imprime.
    path("preview/", PreviewLabelView.as_view(), name="label-preview"),
    # SVG suelto de un QR/código de barras (preview del editor + debug).
    path("barcode/", BarcodeImageView.as_view(), name="label-barcode"),
    # Generación por lote: muchos rótulos -> un Document (apps.documents).
    path("batch/", LabelBatchView.as_view(), name="label-batch"),
    # Familias tipográficas para el editor de plantillas de ElementLayout (no
    # es un recurso con CRUD, va como vista suelta).
    path("fonts/", AvailableFontsView.as_view(), name="fonts"),
] + router.urls
