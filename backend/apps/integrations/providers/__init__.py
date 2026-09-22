"""Registro de plataformas de tienda online (ver ``base.StoreProvider``)."""

from .base import NormalizedOrder, StoreProvider
from .tiendanube import TiendanubeProvider

_PROVIDERS = {provider.platform: provider for provider in (TiendanubeProvider(),)}


def get_provider(platform):
    """Proveedor de ``platform`` (el valor de ``StoreConnection.Platform``)."""
    try:
        return _PROVIDERS[platform]
    except KeyError:
        raise ValueError(f"Plataforma de tienda no soportada: {platform!r}.") from None


__all__ = ["NormalizedOrder", "StoreProvider", "get_provider"]
