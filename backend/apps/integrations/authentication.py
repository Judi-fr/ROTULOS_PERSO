"""Autenticación de máquina (``Authorization: Api-Key <clave>``) para los
endpoints de ingesta (``views.IngestOrderView``). NO reemplaza ni toca el
JWT del resto de la API (``apps.accounts.authentication``): esta clase
solo se declara explícitamente en las vistas que la necesitan, conviven
sin interferirse.
"""

from __future__ import annotations

from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import IntegrationKey

# Largo del prefijo guardado en IntegrationKey.prefix: alcanza para
# encontrar la fila candidata sin escanear toda la tabla, sin exponer
# suficiente clave como para que el prefijo solo sirva de algo.
PREFIX_LENGTH = 10


class ApiKeyAuthentication(BaseAuthentication):
    """Header ``Authorization: Api-Key <clave>``. Busca por prefijo,
    compara el HASH completo (nunca la clave en texto plano) y deja
    ``owner`` como ``request.user`` — así el resto de la vista no necesita
    saber que la identidad vino de una API key y no de un JWT."""

    keyword = "Api-Key"

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith(f"{self.keyword} "):
            return None

        raw_key = header[len(self.keyword) + 1 :].strip()
        if not raw_key:
            raise AuthenticationFailed("Clave de API vacía.")

        prefix = raw_key[:PREFIX_LENGTH]
        key_hash = IntegrationKey.hash_key(raw_key)
        try:
            integration_key = IntegrationKey.objects.select_related("owner").get(
                prefix=prefix, key_hash=key_hash, is_active=True
            )
        except IntegrationKey.DoesNotExist:
            raise AuthenticationFailed("Clave de API inválida o inactiva.")

        integration_key.last_used_at = timezone.now()
        integration_key.save(update_fields=["last_used_at"])

        return (integration_key.owner, integration_key)

    def authenticate_header(self, request):
        # Header que DRF devuelve en el WWW-Authenticate de un 401, para
        # que un cliente que no manda Authorization reciba un 401 claro
        # en vez de un 403 genérico.
        return self.keyword
