"""Vistas propias de Shopify: la App URL (``shopify/launch/``), la conexión
manual con la app del comerciante (``shopify/connect-manual/``) y la
impresión desde su menú Imprimir (``shopify/print-link/`` +
``shopify/print/<token>``). Lo común (instalación OAuth, webhooks, el PDF del
enlace) está en ``apps.integrations.views``.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.http import HttpResponseRedirect
from django.views.decorators.clickjacking import xframe_options_exempt
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record

from ... import store_print
from ...models import StoreConnection
from ...serializers import StoreConnectionSerializer
from ...stores import connect_with_own_app, make_claim_token, make_oauth_state
from ...views import (
    STORE_CONNECT_PERMISSION,
    _print_document,
    _record_store_connect,
    _store_frontend_redirect,
)
from .. import get_provider
from ..base import OWN_APP_CLIENT_ID_PREF, ProviderAuthError, ProviderError

logger = logging.getLogger(__name__)

from . import admin_print
from .provider import missing_scopes


def _needs_new_scopes(connection):
    """La tienda conectada con NUESTRA app no tiene todos los scopes que la
    app pide hoy. Sin scopes guardados no se sabe, y no se insiste (si no,
    cada apertura sería un OAuth). Una conectada con la app propia del
    comerciante no se puede re-aprobar desde acá: sus scopes los decide él."""
    if not connection.scopes or (connection.preferences or {}).get(OWN_APP_CLIENT_ID_PREF):
        return False
    return bool(missing_scopes(connection.scopes, getattr(settings, "SHOPIFY_SCOPES", "")))


class ShopifyLaunchView(APIView):
    """GET /api/v1/integrations/shopify/launch/?shop=...&hmac=...&timestamp=...

    La *App URL* de la app en Shopify: adonde llega el comerciante cuando
    instala la app desde la App Store o la abre desde su admin. Viene
    firmada, pero sin nuestro usuario. Si la tienda ya está conectada lo
    manda a nuestra web (o a vincularla, si todavía no tiene dueño); si no,
    arranca el OAuth con un ``state`` firmado SIN usuario y atado a esa
    tienda — Shopify exige autenticar por OAuth antes de mostrar nada, aun
    después de una reinstalación.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        provider = get_provider(StoreConnection.Platform.SHOPIFY)
        if not provider.verify_callback(request.query_params):
            return _store_frontend_redirect({"store_error": "invalid_signature"})
        try:
            shop_domain = provider.callback_shop_domain(request.query_params)
        except ValueError:
            return _store_frontend_redirect({"store_error": "invalid_shop"})

        connection = StoreConnection.objects.filter(
            platform=provider.platform, external_store_id=shop_domain, status=StoreConnection.Status.ACTIVE
        ).first()
        if connection is not None:
            if connection.owner_id is None:
                return _store_frontend_redirect({"store_claim": make_claim_token(connection)})
            if not _needs_new_scopes(connection):
                return _store_frontend_redirect({})
            # Instalada antes de que la app pidiera un scope nuevo (p. ej.
            # write_fulfillments para informar "entregado"): se pasa otra vez
            # por el OAuth, Shopify le muestra al comerciante solo lo que
            # falta aprobar y el callback actualiza el token de la misma fila.

        try:
            url = provider.build_authorize_url(
                make_oauth_state(None, provider.platform, shop_domain), shop_domain=shop_domain
            )
        except ProviderError:
            logger.exception("No se pudo iniciar la instalación de Shopify")
            return _store_frontend_redirect({"store_error": "provider_unavailable"})
        return HttpResponseRedirect(url)


class ShopifyManualConnectView(APIView):
    """POST /api/v1/integrations/shopify/connect-manual/
    ``{"shop", "client_id", "client_secret"}``

    Conexión manual de Shopify, sin instalar nuestra app: el comerciante creó
    una app en su propio Dev Dashboard (con los permisos y el acceso a datos
    de clientes que necesitamos), la instaló en su tienda y pega su client ID
    y su secreto. Se prueban en el momento pidiendo un token
    (``stores.connect_with_own_app``). La impresión desde el menú Imprimir de
    Shopify no funciona por este camino: esa extensión es de nuestra app.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(STORE_CONNECT_PERMISSION)]

    def post(self, request):
        provider = get_provider("shopify")
        data = request.data if isinstance(request.data, dict) else {}
        try:
            shop = provider.normalize_shop_domain(data.get("shop", ""))
        except ValueError as exc:
            return Response({"shop": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        client_id = str(data.get("client_id") or "").strip()
        client_secret = str(data.get("client_secret") or "").strip()
        if not client_id or not client_secret:
            return Response(
                {"detail": "Pegá el ID de cliente y el secreto de tu app (Dev Dashboard → tu app → Configuración)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = connect_with_own_app(provider, request.user, shop, client_id, client_secret)
        except ProviderAuthError as exc:
            return Response({"detail": _own_app_error_message(str(exc))}, status=status.HTTP_400_BAD_REQUEST)
        except ProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
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


def _own_app_error_message(detail):
    """Lo que Shopify contesta cuando no da el token, dicho para el
    comerciante. Los códigos son los de su OAuth (``shop_not_permitted``,
    ``invalid_client``...)."""
    if "shop_not_permitted" in detail:
        return (
            "Shopify no deja usar esa app en esta tienda: la app tiene que estar creada en la misma "
            "organización que la tienda e instalada en ella."
        )
    if any(code in detail for code in ("application_cannot_be_found", "invalid_client", "Could not find Shopify API application")):
        return "Shopify no reconoce esas credenciales. Revisá que el ID de cliente y el secreto sean de tu app."
    return f"Shopify no aceptó esas credenciales. {detail}"


class ShopifyPrintLinkView(APIView):
    """POST /api/v1/integrations/shopify/print-link/  ``{"ids": ["gid://shopify/Order/…", …], "action"?}``

    ``action``: ``labels`` (por defecto), ``manifest`` o ``dispatch`` (ver
    ``store_print.ACTIONS``). La planilla sale de una segunda opción del menú
    Imprimir y despachar, de una acción masiva de la lista de pedidos.

    Lo llama la extensión de impresión del admin de Shopify (ver
    ``store_print``). Sin JWT nuestro: la identidad es el ID token de
    Shopify que viaja en ``Authorization: Bearer``, y de él sale la tienda.
    Devuelve el enlace de vida corta que la vista previa de impresión carga.
    Abierto a CORS (solo esta ruta, ver ``apps.IntegrationsConfig``): la
    extensión corre en un origen de Shopify.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not token:
            return Response({"detail": "Falta la sesión de Shopify."}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            shop_domain = admin_print.verify_id_token(token)
        except store_print.PrintError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            action = store_print.parse_action((request.data or {}).get("action"))
            connection = admin_print.connection_for_shop(shop_domain)
            legacy_ids = admin_print.legacy_order_ids((request.data or {}).get("ids"))
            orders, missing = store_print.resolve_orders(connection, legacy_ids)
            if not orders:
                raise store_print.PrintError("Ninguno de los pedidos elegidos existe en Shopify.")
            url, summary = store_print.action_link(connection, orders, action, route="shopify-print")
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
            changes={f"shopify_{action}": {"from": None, "to": len(orders)}},
        )
        return Response({"url": url, "count": len(orders), "missing": missing, "action": action, **summary})


@xframe_options_exempt
def shopify_print_document(request, token):
    """GET /api/v1/integrations/shopify/print/<token>

    El PDF que muestra la vista previa de impresión de Shopify. Lo carga el
    navegador dentro del admin de Shopify (de ahí el ``xframe_options_exempt``:
    con el DENY por defecto la vista previa quedaría en blanco). Lo
    autentica el enlace firmado; sin sesión, sin JWT."""
    return _print_document(request, token, StoreConnection.Platform.SHOPIFY)
