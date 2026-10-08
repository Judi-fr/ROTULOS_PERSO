"""Conexión manual de Magento: ``POST /api/v1/integrations/magento/connect-manual/``.

Reutiliza las piezas comunes de las demás conexiones con claves
(``stores.connect_with_api_credentials``, la auditoría y el serializer de
``apps.integrations.views``).
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.role_permissions import HasRolePermission

from ...models import StoreConnection
from ...serializers import StoreConnectionSerializer
from ...stores import connect_with_api_credentials
from ...views import STORE_CONNECT_PERMISSION, _record_store_connect
from .. import get_provider
from ..base import ProviderError
from .provider import credentials_token, external_store_id_for


class MagentoManualConnectView(APIView):
    """POST /api/v1/integrations/magento/connect-manual/
    ``{"site_url", "consumer_key", "consumer_secret", "access_token", "access_token_secret"}``

    El camino de Magento (fase 1): el comerciante crea una Integración en su
    admin (Sistema → Extensiones → Integraciones) con los permisos de pedidos
    y envíos, la activa y pega sus cuatro credenciales. Se prueban contra la
    tienda antes de guardarlas.
    """

    CREDENTIAL_FIELDS = ("consumer_key", "consumer_secret", "access_token", "access_token_secret")

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(STORE_CONNECT_PERMISSION)]

    def post(self, request):
        provider = get_provider(StoreConnection.Platform.MAGENTO)
        data = request.data if isinstance(request.data, dict) else {}
        try:
            site = provider.normalize_shop_domain(data.get("site_url", ""))
        except ValueError as exc:
            return Response({"site_url": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)

        creds = {field: str(data.get(field) or "").strip() for field in self.CREDENTIAL_FIELDS}
        if not all(creds.values()):
            return Response(
                {"detail": "Pegá las cuatro credenciales de la integración (clave y secreto del consumidor, token de acceso y su secreto)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            rest_path = provider.check_credentials(site, creds)
        except ProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        result = connect_with_api_credentials(
            provider,
            request.user,
            external_store_id=external_store_id_for(site),
            store_url=site,
            token=credentials_token(*(creds[field] for field in self.CREDENTIAL_FIELDS)),
            preferences={"magento_rest_path": rest_path},
        )
        if result.owner_conflict:
            return Response(
                {"detail": "Esa tienda ya está vinculada a otra cuenta. Si es tuya, escribinos desde Ayuda / Soporte."},
                status=status.HTTP_409_CONFLICT,
            )
        _record_store_connect(request, request.user, result.connection)
        return Response(
            StoreConnectionSerializer(result.connection, context={"request": request}).data,
            status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK,
        )
