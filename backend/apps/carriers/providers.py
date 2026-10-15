"""Los transportistas con los que se puede despachar: ``get_provider(code)`` y
``all_providers()``, como ``apps.integrations.providers`` para las tiendas."""

from __future__ import annotations

from .andreani.provider import AndreaniProvider
from .correo_argentino.provider import CorreoArgentinoProvider
from .oca.provider import OcaProvider

_PROVIDERS = {provider.code: provider for provider in (AndreaniProvider(), CorreoArgentinoProvider(), OcaProvider())}


def get_provider(code):
    """El proveedor del transportista ``code``, o ``None``."""
    return _PROVIDERS.get(str(code or ""))


def all_providers():
    return list(_PROVIDERS.values())
