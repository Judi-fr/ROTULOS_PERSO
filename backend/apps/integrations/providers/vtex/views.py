"""Conexión manual de VTEX: ``POST /api/v1/integrations/vtex/connect-manual/``.

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
from .provider import credentials_token, store_url_for


class VtexManualConnectView(APIView):
    """POST /api/v1/integrations/vtex/connect-manual/
    ``{"account", "app_key", "app_token"}``

    El único camino de VTEX: el comerciante crea una clave de aplicación en su
    admin, le asigna un rol con los permisos de pedidos y la pega. Se prueba
    contra VTEX antes de guardarla. La respuesta suma ``warnings`` con los
    permisos que faltan sin ser imprescindibles para conectar.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(STORE_CONNECT_PERMISSION)]

    def post(self, request):
        provider = get_provider(StoreConnection.Platform.VTEX)
        data = request.data if isinstance(request.data, dict) else {}
        try:
            account = provider.normalize_shop_domain(data.get("account", ""))
        except ValueError as exc:
            return Response({"account": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)

        app_key = str(data.get("app_key") or "").strip()
        app_token = str(data.get("app_token") or "").strip()
        if not app_key or not app_token:
            return Response(
                {"detail": "Pegá el appKey y el appToken de la clave que creaste en VTEX."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            warnings = provider.check_credentials(account, app_key, app_token)
        except ProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        result = connect_with_api_credentials(
            provider,
            request.user,
            external_store_id=account,
            store_url=store_url_for(account),
            token=credentials_token(app_key, app_token),
        )
        if result.owner_conflict:
            return Response(
                {"detail": "Esa tienda ya está vinculada a otra cuenta. Si es tuya, escribinos desde Ayuda / Soporte."},
                status=status.HTTP_409_CONFLICT,
            )
        _record_store_connect(request, request.user, result.connection)
        payload = dict(StoreConnectionSerializer(result.connection, context={"request": request}).data)
        payload["warnings"] = warnings
        return Response(payload, status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK)
