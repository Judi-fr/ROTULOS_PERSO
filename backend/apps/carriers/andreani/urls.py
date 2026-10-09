from django.urls import path

from .views import (
    AccountTestView,
    AccountView,
    BranchesView,
    CheckoutTestView,
    CheckoutView,
    ShipmentCancelView,
    ShipmentCollectionView,
    ShipmentDetailView,
    ShipmentLabelsView,
    ShipmentPrintView,
    ShipmentLabelView,
    ShipmentQuoteView,
    ShipmentRefreshView,
)

urlpatterns = [
    path("account/", AccountView.as_view(), name="andreani-account"),
    path("account/test/", AccountTestView.as_view(), name="andreani-account-test"),
    path("checkout/", CheckoutView.as_view(), name="andreani-checkout"),
    path("checkout/test/", CheckoutTestView.as_view(), name="andreani-checkout-test"),
    path("branches/", BranchesView.as_view(), name="andreani-branches"),
    path("shipments/", ShipmentCollectionView.as_view(), name="andreani-shipments"),
    path("shipments/quote/", ShipmentQuoteView.as_view(), name="andreani-shipment-quote"),
    path("shipments/labels/", ShipmentLabelsView.as_view(), name="andreani-shipment-labels"),
    path("shipments/print/", ShipmentPrintView.as_view(), name="andreani-shipment-print"),
    path("shipments/<int:pk>/", ShipmentDetailView.as_view(), name="andreani-shipment"),
    path("shipments/<int:pk>/label/", ShipmentLabelView.as_view(), name="andreani-shipment-label"),
    path("shipments/<int:pk>/refresh/", ShipmentRefreshView.as_view(), name="andreani-shipment-refresh"),
    path("shipments/<int:pk>/cancel/", ShipmentCancelView.as_view(), name="andreani-shipment-cancel"),
]
