"""Conexión manual de Magento: ``POST /api/v1/integrations/magento/connect-manual/``,
y lo que llama nuestro módulo (fase 2, ``admin_module.py``): ``print-link/``,
``print/<token>``, ``rates/`` y la descarga del módulo (``module/``).

Reutiliza las piezas comunes de las demás conexiones con claves
(``stores.connect_with_api_credentials``, la auditoría y el serializer de
``apps.integrations.views``).
"""

from __future__ import annotations

import logging

from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.role_permissions import HasRolePermission
from apps.accounts.teams import data_owner
from apps.audit.services import record

from ... import store_print
from ...models import StoreConnection
from ...serializers import StoreConnectionSerializer
from ...stores import connect_with_api_credentials
from ...views import STORE_CONNECT_PERMISSION, _print_document, _record_store_connect
from .. import get_provider
from ..base import ProviderError

# La cotización es la misma que la del plugin de WooCommerce (mismos campos,
# misma tabla y regla de shipping_rates): se reusa tal cual.
from ..woocommerce.rates import quote
from . import admin_module
from .provider import credentials_token, external_store_id_for

logger = logging.getLogger(__name__)


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
            data_owner(request.user),
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


# --- Fase 2: nuestro módulo (admin_module.py) ---------------------------------


class MagentoModuleDownloadView(APIView):
    """GET /api/v1/integrations/magento/module/

    El módulo "Rotulos_Envio" como zip (``admin_module.module_zip``), para
    descomprimir en la raíz de la tienda y activar con ``bin/magento``."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(STORE_CONNECT_PERMISSION)]

    def get(self, request):
        response = HttpResponse(admin_module.module_zip(), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{admin_module.MODULE_ZIP_NAME}"'
        return response


class MagentoPrintLinkView(APIView):
    """POST /api/v1/integrations/magento/print-link/  ``{"store", "ids", "ts", "action"?}``

    Lo llama la acción masiva de nuestro módulo desde el servidor de la tienda,
    firmado con su secreto (``X-Rotulos-Signature``), igual que el plugin de
    WooCommerce. ``ids`` son los ``entity_id`` de los pedidos elegidos (nuestro
    ``external_id``). Devuelve el enlace de vida corta al PDF."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            connection, data = admin_module.verify_module_request(
                request.body, request.headers.get(admin_module.SIGNATURE_HEADER, "")
            )
        except store_print.PrintError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            action = store_print.parse_action(data.get("action"))
            orders, missing = store_print.resolve_orders(connection, store_print.order_ids(data.get("ids")))
            if not orders:
                raise store_print.PrintError("Ninguno de los pedidos elegidos existe en la tienda.")
            url, summary = store_print.action_link(connection, orders, action, route="magento-print")
        except store_print.PrintError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        record(
            request,
            actor=connection.owner,
            category="labels",
            action="label.batch",
            target=connection,
            target_type="storeconnection",
            target_repr=str(connection),
            changes={f"magento_{action}": {"from": None, "to": len(orders)}},
        )
        return Response({"url": url, "count": len(orders), "missing": missing, "action": action, **summary})


class MagentoRatesView(APIView):
    """POST /api/v1/integrations/magento/rates/
    ``{"store", "ts", "postcode", "country", "weight_kg", "cart_total", "currency"}``

    Lo llama el método de envío de nuestro módulo desde el checkout. Está en
    medio de la venta de otro: salvo una firma inválida (401), NUNCA contesta
    error — una lista vacía solo saca nuestra opción del checkout. Con el
    límite de las cotizaciones (``store_rates``), no el anónimo de 60/min: una
    tienda con mucho tráfico se quedaría sin opción de envío."""

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "store_rates"

    def post(self, request):
        try:
            connection, data = admin_module.verify_module_request(
                request.body, request.headers.get(admin_module.SIGNATURE_HEADER, "")
            )
        except store_print.PrintError as exc:
            return Response({"detail": str(exc), "rates": []}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            return Response(quote(connection, data))
        except Exception:  # noqa: BLE001 - nunca romper un checkout ajeno
            logger.exception("Error inesperado cotizando para la tienda %s", connection.pk)
            return Response({"rates": []})


def magento_print_document(request, token):
    """GET /api/v1/integrations/magento/print/<token>

    El PDF al que el módulo manda el navegador. Lo autentica el enlace
    firmado; sin sesión, sin JWT."""
    return _print_document(request, token, StoreConnection.Platform.MAGENTO)
