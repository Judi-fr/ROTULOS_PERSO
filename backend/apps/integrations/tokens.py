"""Tokens de tienda que vencen (hoy Shopify: el access token dura una hora).

Tiendanube da un token que no vence y esto no lo toca: sin
``token_expires_at`` el token guardado se usa tal cual.

Con vencimiento, ``access_token_for`` renueva un poco ANTES de que venza
(``REFRESH_MARGIN``), así ningún pedido a la API sale con un token que
caduca en el camino. La renovación se hace con la fila bloqueada
(``select_for_update``) porque el refresh token ROTA: si el webhook y el
worker renovaran a la vez, uno se quedaría con un refresh token que la
plataforma ya invalidó. El segundo que llega encuentra el token ya
renovado y lo usa.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import StoreConnection

REFRESH_MARGIN = timedelta(seconds=60)


def _is_fresh(connection, now):
    return connection.token_expires_at is None or connection.token_expires_at - REFRESH_MARGIN > now


def access_token_for(connection, provider, *, force_refresh=False):
    """Token vigente de ``connection``, renovándolo si hace falta.

    ``force_refresh`` renueva aunque el vencimiento guardado diga que sigue
    vivo: es para cuando la plataforma ya lo rechazó con 401. Un token sin
    vencimiento nunca se renueva (no hay con qué). ``ProviderAuthError`` si
    la plataforma rechaza el refresh token: la tienda hay que reinstalarla.
    """
    now = timezone.now()
    if connection.token_expires_at is None:
        return connection.access_token
    if not force_refresh and _is_fresh(connection, now):
        return connection.access_token

    stale_token = connection.access_token_encrypted
    with transaction.atomic():
        locked = StoreConnection.objects.select_for_update().get(pk=connection.pk)
        # Otro proceso ya lo renovó mientras esperábamos el lock.
        already_renewed = locked.access_token_encrypted != stale_token
        if already_renewed or (not force_refresh and _is_fresh(locked, now)):
            _copy_tokens(locked, connection)
            return connection.access_token

        oauth = provider.refresh_access_token(locked)
        locked.set_tokens(oauth)
        locked.save(update_fields=[*StoreConnection.TOKEN_FIELDS, "updated_at"])
        _copy_tokens(locked, connection)
    return connection.access_token


def _copy_tokens(source, target):
    for field in StoreConnection.TOKEN_FIELDS:
        setattr(target, field, getattr(source, field))
