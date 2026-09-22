"""Rótulos pedidos desde el admin de la tienda. Se monta bajo
``/api/v1/integrations/`` (ver config/urls.py).

Deliberadamente SEPARADO de ``urls.py`` (el ABM admin, con JWT), igual que
``ingest_urls``: estas vistas las llama Tiendanube y tienen su propia
autenticación (el token firmado de la URL, ver ``store_labels``).

Sin barra final: la plataforma arma cada URL pegándole un sufijo a la base
que registramos como ``callback_labels_url``, y el redirect de ``APPEND_SLASH``
rompería el POST.
"""

from django.urls import path

from .label_views import (
    StoreLabelCancelView,
    StoreLabelDownloadView,
    StoreLabelGenerateView,
    StoreLabelReactivateView,
    StoreLabelSuspensionView,
)

urlpatterns = [
    path(
        "tiendanube/labels/download/<str:token>",
        StoreLabelDownloadView.as_view(),
        name="tiendanube-label-download",
    ),
    path(
        "tiendanube/labels/<str:token>/generate",
        StoreLabelGenerateView.as_view(),
        name="tiendanube-label-generate",
    ),
    path(
        "tiendanube/labels/<str:token>/cancel",
        StoreLabelCancelView.as_view(),
        name="tiendanube-label-cancel",
    ),
    path(
        "tiendanube/labels/<str:token>/suspension",
        StoreLabelSuspensionView.as_view(),
        name="tiendanube-label-suspension",
    ),
    path(
        "tiendanube/labels/<str:token>/reactivate",
        StoreLabelReactivateView.as_view(),
        name="tiendanube-label-reactivate",
    ),
]
