"""Cotización de envíos. Se monta bajo ``/api/v1/integrations/``.

Aparte de ``urls.py`` (ABM con JWT) y de ``label_urls.py`` (rótulos), por
la misma razón que aquel: esta vista la llama la plataforma y se autentica
con el token firmado de la URL, no con una sesión.

Sin barra final, igual que los rótulos: la URL se registra tal cual como
``callback_url`` del carrier y el redirect de ``APPEND_SLASH`` rompería el
POST.
"""

from django.urls import path

from .rate_views import TiendanubeRatesView

urlpatterns = [
    path(
        "tiendanube/rates/<str:token>",
        TiendanubeRatesView.as_view(),
        name="tiendanube-rates",
    ),
]
