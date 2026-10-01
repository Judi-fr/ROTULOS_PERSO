"""Registro de plataformas de tienda online (ver ``base.StoreProvider``)."""

from .base import NormalizedOrder, StoreProvider
from .shopify import ShopifyProvider
from .tiendanube import TiendanubeProvider
from .woocommerce import WooCommerceProvider

_PROVIDERS = {
    provider.platform: provider
    for provider in (TiendanubeProvider(), ShopifyProvider(), WooCommerceProvider())
}


def get_provider(platform):
    """Proveedor de ``platform`` (el valor de ``StoreConnection.Platform``)."""
    try:
        return _PROVIDERS[platform]
    except KeyError:
        raise ValueError(f"Plataforma de tienda no soportada: {platform!r}.") from None


def all_providers():
    """Todos los proveedores registrados (para registrar sus handlers y URLs)."""
    return tuple(_PROVIDERS.values())


__all__ = ["NormalizedOrder", "StoreProvider", "all_providers", "get_provider"]
