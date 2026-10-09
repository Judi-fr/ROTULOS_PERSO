"""Vistas propias de WooCommerce: la autorización automática
(``woocommerce/keys/`` + ``woocommerce/return/``), las claves pegadas a mano
(``woocommerce/connect-manual/``) y lo que usa nuestro plugin de WordPress
(``woocommerce/print-plugin/``, ``print-link/``, ``print/<token>``,
``rates/``). Lo común está en ``apps.integrations.views``.
"""

from __future__ import annotations

import logging

from django.core import signing
from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record

from ... import store_print
from ...models import StoreConnection
from ...serializers import StoreConnectionSerializer
from ...stores import connect_with_credentials, read_oauth_state_site
from ...views import (
    STORE_CONNECT_PERMISSION,
    _print_document,
    _record_store_connect,
    _store_frontend_redirect,
)
from .. import get_provider
from ..base import ProviderAuthError, ProviderError

logger = logging.getLogger(__name__)

from . import admin_print, rates
from .provider import external_store_id_for, valid_key_pair


def _woocommerce():
    return get_provider(StoreConnection.Platform.WOOCOMMERCE)


class WooCommerceKeysView(APIView):
    """POST /api/v1/integrations/woocommerce/keys/

    El ``callback_url`` de la autorización automática: cuando el comerciante
    aprueba en su sitio, WooCommerce nos POSTea (servidor a servidor) un JSON
    con ``consumer_key``, ``consumer_secret`` y nuestro ``state`` en
    ``user_id``. El ``state`` firmado dice qué usuario y qué sitio (lo
    generó ``woocommerce/install-url/``). Contesta enseguida y sin llamar a
    la tienda: ella está esperando esta respuesta para seguir.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        try:
            user, site = read_oauth_state_site(str(data.get("user_id") or ""), StoreConnection.Platform.WOOCOMMERCE)
        except signing.BadSignature:
            return Response({"detail": "Autorización vencida o inválida."}, status=status.HTTP_400_BAD_REQUEST)
        if user is None:
            return Response({"detail": "Autorización sin usuario."}, status=status.HTTP_400_BAD_REQUEST)

        key, secret = str(data.get("consumer_key") or ""), str(data.get("consumer_secret") or "")
        if not valid_key_pair(key, secret):
            return Response({"detail": "Faltan las claves de la API."}, status=status.HTTP_400_BAD_REQUEST)
        if str(data.get("key_permissions") or "read_write") != "read_write":
            return Response({"detail": "Las claves tienen que ser de lectura y escritura."}, status=status.HTTP_400_BAD_REQUEST)

        result = connect_with_credentials(_woocommerce(), user, site, key, secret)
        _record_store_connect(request, user, result.connection)
        return Response({"received": True})


class WooCommerceReturnView(APIView):
    """GET /api/v1/integrations/woocommerce/return/?success=1&user_id=<state>

    El ``return_url``: adonde WooCommerce manda el navegador del comerciante
    después de aprobar (o rechazar). Las claves ya llegaron por
    ``WooCommerceKeysView``; esto solo lo devuelve a nuestra web con el
    resultado, como el callback de las otras plataformas.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        if str(request.query_params.get("success") or "") != "1":
            return _store_frontend_redirect({"store_error": "authorization_cancelled"})
        try:
            user, site = read_oauth_state_site(
                request.query_params.get("user_id", ""), StoreConnection.Platform.WOOCOMMERCE
            )
        except signing.BadSignature:
            return _store_frontend_redirect({"store_error": "invalid_state"})

        connection = StoreConnection.objects.filter(
            platform=StoreConnection.Platform.WOOCOMMERCE, external_store_id=external_store_id_for(site)
        ).first()
        if connection is None:
            # Aprobó pero las claves no llegaron (la tienda no pudo
            # POSTearlas a nuestro servidor).
            return _store_frontend_redirect({"store_error": "provider_unavailable"})
        if user is not None and connection.owner_id not in (None, user.pk):
            return _store_frontend_redirect({"store_error": "owned_by_other_account"})
        return _store_frontend_redirect({"store_connected": str(connection.pk)})


class WooCommerceManualConnectView(APIView):
    """POST /api/v1/integrations/woocommerce/connect-manual/
    ``{"site_url", "consumer_key", "consumer_secret"}``

    El camino manual: el comerciante creó las claves en su admin de
    WooCommerce (Ajustes → Avanzado → API REST, permisos de lectura y
    escritura) y las pega. Acá sí se prueban contra la tienda antes de
    guardarlas: es nuestro pedido, nadie está esperando del otro lado.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(STORE_CONNECT_PERMISSION)]

    def post(self, request):
        provider = _woocommerce()
        data = request.data if isinstance(request.data, dict) else {}
        try:
            site = provider.normalize_shop_domain(data.get("site_url", ""))
        except ValueError as exc:
            return Response({"site_url": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)

        key = str(data.get("consumer_key") or "").strip()
        secret = str(data.get("consumer_secret") or "").strip()
        if not valid_key_pair(key, secret):
            return Response(
                {"detail": "Las claves no tienen el formato de WooCommerce (la clave empieza con ck_ y el secreto con cs_)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            auth_mode = provider.check_credentials(site, key, secret)
        except ProviderAuthError:
            return Response(
                {"detail": "La tienda rechazó las claves. Revisá que estén bien copiadas y que tengan permiso de lectura y escritura."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        result = connect_with_credentials(provider, request.user, site, key, secret, auth_mode=auth_mode)
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


class WooCommercePluginDownloadView(APIView):
    """GET /api/v1/integrations/woocommerce/print-plugin/

    El plugin de WordPress "Rótulos de envío" como zip, listo para subir en
    Plugins → Añadir nuevo → Subir plugin (ver ``admin_print.plugin_zip``)."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(STORE_CONNECT_PERMISSION)]

    def get(self, request):
        response = HttpResponse(admin_print.plugin_zip(), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{admin_print.PLUGIN_ZIP_NAME}"'
        return response


class WooCommercePrintLinkView(APIView):
    """POST /api/v1/integrations/woocommerce/print-link/  ``{"store", "ids", "ts", "action"?}``

    ``action``: ``labels`` (por defecto), ``manifest`` o ``dispatch`` — una
    acción masiva del plugin por cada una (``store_print.ACTIONS``).

    Lo llama nuestro plugin de WordPress desde el servidor de la tienda (ver
    ``admin_print``). Sin JWT: la identidad es la firma
    ``X-Rotulos-Signature`` con el secreto de la tienda. Devuelve el enlace de
    vida corta al PDF, al que el plugin manda el navegador. Sin CORS: no lo
    llama un navegador."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        raw_body = request.body
        try:
            connection, data = admin_print.verify_plugin_request(
                raw_body, request.headers.get(admin_print.SIGNATURE_HEADER, "")
            )
        except store_print.PrintError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            action = store_print.parse_action(data.get("action"))
            orders, missing = store_print.resolve_orders(connection, store_print.order_ids(data.get("ids")))
            if not orders:
                raise store_print.PrintError("Ninguno de los pedidos elegidos existe en la tienda.")
            url, summary = store_print.action_link(connection, orders, action, route="woocommerce-print")
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
            changes={f"woocommerce_{action}": {"from": None, "to": len(orders)}},
        )
        return Response({"url": url, "count": len(orders), "missing": missing, "action": action, **summary})


class WooCommerceRatesView(APIView):
    """POST /api/v1/integrations/woocommerce/rates/
    ``{"store", "ts", "postcode", "country", "weight_kg", "currency"}``

    Lo llama el método de envío de nuestro plugin desde el checkout de la
    tienda, firmado igual que la impresión (``admin_print``). Contesta
    ``{"rates": [...]}`` desde la tabla de tarifas (``rates``).
    Está en medio de la venta de otro: salvo una firma inválida (401), NUNCA
    contesta error — una lista vacía solo saca nuestra opción del checkout.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            connection, data = admin_print.verify_plugin_request(
                request.body, request.headers.get(admin_print.SIGNATURE_HEADER, "")
            )
        except store_print.PrintError as exc:
            return Response({"detail": str(exc), "rates": []}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            return Response(rates.quote(connection, data))
        except Exception:  # noqa: BLE001 - nunca romper un checkout ajeno
            logger.exception("Error inesperado cotizando para la tienda %s", connection.pk)
            return Response({"rates": []})


def woocommerce_print_document(request, token):
    """GET /api/v1/integrations/woocommerce/print/<token>

    El PDF al que el plugin manda el navegador (en una pestaña nueva, no
    enmarcado). Lo autentica el enlace firmado; sin sesión, sin JWT."""
    return _print_document(request, token, StoreConnection.Platform.WOOCOMMERCE)
