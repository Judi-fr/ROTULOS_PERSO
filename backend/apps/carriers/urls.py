"""Transportistas. Se monta bajo ``/api/v1/carriers/`` (ver config/urls.py).

Primero las rutas propias de Andreani (anteriores a la capa común: su checkout
y su API original), después la API común para todos (``views.py``), el precio en
el checkout de las tiendas (``checkout_views.py``) y las novedades push que nos
manda un transportista (``push_views.py``)."""

from django.urls import include, path

from . import catalog_views, checkout_views, push_views, views

urlpatterns = [
    path("andreani/", include("apps.carriers.andreani.urls")),
    path("", views.CarrierListView.as_view(), name="carriers"),
    # Catálogo global de transportistas sin API (lo edita el admin).
    path(
        "manual-carriers/",
        catalog_views.ManualCarrierViewSet.as_view({"get": "list", "post": "create"}),
        name="manual-carriers",
    ),
    path(
        "manual-carriers/<int:pk>/",
        catalog_views.ManualCarrierViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
        name="manual-carrier",
    ),
    path("checkout/", checkout_views.CheckoutView.as_view(), name="carrier-checkout"),
    path("checkout/test/", checkout_views.CheckoutTestView.as_view(), name="carrier-checkout-test"),
    path("shipments/", views.ShipmentCollectionView.as_view(), name="carrier-shipments"),
    path("shipments/quote/", views.ShipmentQuoteView.as_view(), name="carrier-shipment-quote"),
    path("shipments/print/", views.ShipmentPrintView.as_view(), name="carrier-shipment-print"),
    path("shipments/<int:pk>/", views.ShipmentDetailView.as_view(), name="carrier-shipment"),
    path("shipments/<int:pk>/label/", views.ShipmentLabelView.as_view(), name="carrier-shipment-label"),
    path("shipments/<int:pk>/refresh/", views.ShipmentRefreshView.as_view(), name="carrier-shipment-refresh"),
    path("shipments/<int:pk>/cancel/", views.ShipmentCancelView.as_view(), name="carrier-shipment-cancel"),
    # Con un prefijo propio, no ``<carrier>/account/``: así no chocan con las
    # rutas viejas de Andreani (``andreani/account/`` es otra vista).
    path("accounts/<str:carrier>/", views.AccountView.as_view(), name="carrier-account"),
    path("accounts/<str:carrier>/test/", views.AccountTestView.as_view(), name="carrier-account-test"),
    path("accounts/<str:carrier>/push/", push_views.PushTokenView.as_view(), name="carrier-push-token"),
    path("branches/<str:carrier>/", views.BranchesView.as_view(), name="carrier-branches"),
    path("push/<str:carrier>/<str:key>/", push_views.PushReceiveView.as_view(), name="carrier-push"),
]
