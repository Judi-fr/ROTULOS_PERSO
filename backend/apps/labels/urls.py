"""Rutas de rótulos y plantillas. Se montan bajo ``/api/v1/labels/``
(ver config/urls.py), dando URLs como ``/api/v1/labels/templates/``. SimpleRouter porque la API es JSON pura,
mismo patrón que ``apps.orders.urls``.
"""

from django.urls import path
from rest_framework.routers import SimpleRouter

from .catalog_views import LabelSizeViewSet

from .batch_views import LabelBatchView
from .views import (
    BarcodeImageView,
    LabelImageViewSet,
    LabelSequenceView,
    LabelTemplateViewSet,
    PreviewContextView,
    PreviewLabelView,
    RenderLabelView,
)

router = SimpleRouter()
router.register(r"templates", LabelTemplateViewSet, basename="label-template")
router.register(r"images", LabelImageViewSet, basename="label-image")
router.register(r"sizes", LabelSizeViewSet, basename="label-size")

urlpatterns = [
    # Render sin persistir (plantilla + pedido -> PDF), semilla del lote.
    path("render/", RenderLabelView.as_view(), name="label-render"),
    # Vista previa de un diseño sin guardar, con datos de muestra: es lo que
    # hace que el editor muestre lo mismo que se imprime.
    path("preview/", PreviewLabelView.as_view(), name="label-preview"),
    path("preview-context/", PreviewContextView.as_view(), name="label-preview-context"),
    # SVG suelto de un QR/código de barras (preview del editor + debug).
    path("barcode/", BarcodeImageView.as_view(), name="label-barcode"),
    # Generación por lote: muchos rótulos -> un Document (apps.documents).
    path("batch/", LabelBatchView.as_view(), name="label-batch"),
    # Numeración de {{secuencia}}: prefijo, ceros y próximo número (editor).
    path("sequence/", LabelSequenceView.as_view(), name="label-sequence"),
] + router.urls
