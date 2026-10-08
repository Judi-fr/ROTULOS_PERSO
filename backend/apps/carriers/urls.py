"""Transportistas. Se monta bajo ``/api/v1/carriers/`` (ver config/urls.py):
cada uno con sus rutas, desde su carpeta."""

from django.urls import include, path

urlpatterns = [
    path("andreani/", include("apps.carriers.andreani.urls")),
]
