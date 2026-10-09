"""Vistas de integraciones.

ABM de claves/webhooks (``IntegrationKeyViewSet``, ``IncomingWebhookViewSet``,
``WebhookEndpointViewSet``, ``WebhookDeliveryListView``): panel admin,
``integrations.manage``, autenticación JWT normal — nada especial acá.

Ingesta (``IngestOrderView``, ``IncomingWebhookView``): estos SÍ son
distintos del resto de la API — ``IngestOrderView`` se autentica con
``Api-Key`` (no JWT) y ``IncomingWebhookView`` no se autentica en absoluto
(la firma HMAC reemplaza al login: cualquiera puede pegarle a la URL, pero
solo pasa si conoce el secreto del webhook).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.core import signing
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record
from apps.orders.ingestion import TARGET_FIELDS, apply_mapping, create_order_from_data, validate_mapped_row
from apps.orders.models import Order

from .authentication import ApiKeyAuthentication
from .models import (
    IncomingWebhook,
    IntegrationKey,
    ShippingRate,
    StoreConnection,
    StoreLabelRequest,
    WebhookDelivery,
    WebhookEndpoint,
)
from .providers import get_provider
from .providers.base import ProviderAuthError, ProviderError
from .serializers import (
    IncomingWebhookSerializer,
    IntegrationKeySerializer,
    ShippingRateSerializer,
    StoreClaimSerializer,
    StoreLabelRequestSerializer,
    StoreSettingsSerializer,
    StoreConnectionSerializer,
    WebhookDeliverySerializer,
    WebhookEndpointSerializer,
)
from .events import enqueue_event
from .stores import (
    INTERNAL_EVENT_PREFIX,
    StoreClaimError,
    claim_store,
    connect_store,
    disconnect_store,
    make_claim_token,
    make_oauth_state,
    make_share_token,
    oauth_state_is_shared,
    read_oauth_state,
    read_share_token,
)
from .webhooks import dispatch_event, webhooks_suspended
from . import shipping_rates, store_print

logger = logging.getLogger(__name__)

# Conectar una tienda es lo que hace entrar sus pedidos: se exige el mismo
# permiso que crear un pedido propio (lo tienen los cuatro roles canónicos).
STORE_CONNECT_PERMISSION = "orders.create"


class IntegrationKeyViewSet(viewsets.ModelViewSet):
    """ABM de claves de API (``integrations.manage``, solo admin). La
    clave completa solo viaja en la respuesta de ``create`` — a partir de
    ahí ni el propio admin puede volver a verla, solo revocarla
    (``is_active=False``) y generar una nueva."""

    serializer_class = IntegrationKeySerializer
    queryset = IntegrationKey.objects.select_related("owner").all()

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("integrations.manage")]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        raw_key = IntegrationKey.generate_raw_key()
        instance = serializer.save(
            key_hash=IntegrationKey.hash_key(raw_key),
            prefix=raw_key[: len(raw_key)][:10],
        )
        record(
            request,
            category="integrations",
            action="integration_key.create",
            target=instance,
            target_type="integrationkey",
            target_repr=str(instance),
        )
        data = self.get_serializer(instance).data
        # Única vez que la clave completa sale de la base: el frontend
        # tiene que mostrarla ahora y avisar que no se puede recuperar.
        data["key"] = raw_key
        headers = self.get_success_headers(data)
        return Response(data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_destroy(self, instance):
        record(
            self.request,
            category="integrations",
            action="integration_key.delete",
            target=instance,
            target_type="integrationkey",
            target_repr=str(instance),
        )
        instance.delete()


class IncomingWebhookViewSet(viewsets.ModelViewSet):
    """ABM de webhooks ENTRANTES configurados (``integrations.manage``,
    solo admin): a qué slug le pega el ERP/tienda, con qué secreto y con
    qué mapeo de campos."""

    serializer_class = IncomingWebhookSerializer
    queryset = IncomingWebhook.objects.select_related("owner", "integration_key").all()

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("integrations.manage")]

    def perform_create(self, serializer):
        instance = serializer.save(secret=IncomingWebhook.generate_secret())
        record(
            self.request,
            category="integrations",
            action="incoming_webhook.create",
            target=instance,
            target_type="incomingwebhook",
            target_repr=str(instance),
        )


class WebhookEndpointViewSet(viewsets.ModelViewSet):
    """ABM de webhooks SALIENTES (``integrations.manage``, solo admin): a
    dónde avisar cuando un pedido cambia de estado."""

    serializer_class = WebhookEndpointSerializer
    queryset = WebhookEndpoint.objects.select_related("owner").all()

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("integrations.manage")]

    def perform_create(self, serializer):
        instance = serializer.save(secret=WebhookEndpoint.generate_secret())
        record(
            self.request,
            category="integrations",
            action="webhook_endpoint.create",
            target=instance,
            target_type="webhookendpoint",
            target_repr=str(instance),
        )


class WebhookDeliveryListView(ListAPIView):
    """GET /api/v1/integrations/webhook-deliveries/ — historial de
    entregas salientes (``integrations.manage``), para ver qué se envió y
    si el cliente respondió. Solo lectura: el reintento (si se agrega más
    adelante) sería una acción aparte, no una edición de este registro."""

    serializer_class = WebhookDeliverySerializer

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("integrations.manage")]

    def get_queryset(self):
        queryset = WebhookDelivery.objects.select_related("endpoint").all()
        endpoint_id = self.request.query_params.get("endpoint", "").strip()
        if endpoint_id.isdigit():
            queryset = queryset.filter(endpoint_id=int(endpoint_id))
        return queryset


def _mapped_results_for_items(owner, items, mapping, source):
    """Crea/recupera un pedido por cada elemento de ``items`` (dict de
    campos ya sea directos o a mapear vía ``mapping``), y arma la
    respuesta por ítem — tanto la API de ingesta como el webhook entrante
    devuelven la MISMA forma de resultado.

    El lote entero queda silenciado con ``webhooks_suspended()`` (una
    llamada con varios ítems no puede encadenar un webhook saliente por
    cada uno, ver ``apps.integrations.webhooks``); al terminar se manda un
    único evento ``orders.imported`` con el resumen de lo creado.
    """
    results = []
    created_order_ids = []
    with webhooks_suspended():
        for item in items:
            if not isinstance(item, dict):
                results.append({"created": False, "error": "Cada ítem debe ser un objeto."})
                continue
            data = apply_mapping(item, mapping) if mapping else {key: item.get(key) for key in TARGET_FIELDS}
            row_errors = validate_mapped_row(data)
            if row_errors:
                results.append(
                    {
                        "external_id": item.get("external_id"),
                        "created": False,
                        "error": " ".join(row_errors),
                    }
                )
                continue
            try:
                order, created = create_order_from_data(owner, data, source=source)
            except Exception as exc:  # noqa: BLE001 - un ítem roto no tumba el resto del lote
                results.append({"external_id": item.get("external_id"), "created": False, "error": str(exc)})
                continue
            if created:
                created_order_ids.append(order.pk)
            results.append(
                {
                    "id": order.pk,
                    "external_id": order.external_id,
                    "created": created,
                    "status": order.status,
                }
            )

    if created_order_ids:
        dispatch_event(
            owner,
            "orders.imported",
            {"count": len(created_order_ids), "source": source, "order_ids": created_order_ids},
        )

    return results


class IngestOrderView(APIView):
    """POST /api/v1/ingest/orders/

    Acepta un pedido (objeto) o una lista de pedidos, con los mismos
    campos que la importación (``apps.orders.ingestion.TARGET_FIELDS``).
    Autenticación por ``Api-Key`` (no JWT, ver ``ApiKeyAuthentication``):
    ``request.user`` termina siendo el ``owner`` de la clave usada, nunca
    un id que mande el cliente. Idempotente por ``external_id``.
    """

    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "ingest"

    def post(self, request):
        payload = request.data
        items = payload if isinstance(payload, list) else [payload]
        if not items:
            raise ValidationError({"detail": "Debe enviarse al menos un pedido."})

        results = _mapped_results_for_items(request.user, items, mapping=None, source=Order.Source.API)
        return Response({"results": results})


class IncomingWebhookView(APIView):
    """POST /api/v1/ingest/webhooks/<slug>/

    Sin autenticación DRF (no hay usuario logueado del otro lado, es un
    ERP/tienda pegándole a una URL): la firma HMAC-SHA256 del header
    ``X-Webhook-Signature`` contra ``IncomingWebhook.secret`` es la única
    verificación, y se hace ANTES de tocar nada — una firma inválida
    devuelve 401 sin crear ningún pedido.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, slug):
        webhook = get_object_or_404(IncomingWebhook, slug=slug, is_active=True)

        raw_body = request.body
        signature = request.META.get("HTTP_X_WEBHOOK_SIGNATURE", "")
        expected = hmac.new(webhook.secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(signature, expected):
            return Response({"detail": "Firma inválida."}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValidationError({"detail": "El payload debe ser JSON válido."})
        if not payload:
            raise ValidationError({"detail": "El payload no puede estar vacío."})

        # Guarda el payload crudo para poder depurar (ver el docstring del
        # módulo/modelo): entra como "changes" de una entrada de auditoría
        # sin actor (no hay usuario logueado del otro lado).
        record(
            None,
            category="orders",
            action="order.webhook_ingest",
            target=webhook,
            target_type="incomingwebhook",
            target_repr=str(webhook),
            changes={"payload": {"from": None, "to": payload}},
        )

        items = payload if isinstance(payload, list) else [payload]
        results = _mapped_results_for_items(
            webhook.owner, items, mapping=webhook.mapping, source=Order.Source.WEBHOOK
        )
        return Response({"results": results})


# ---------------------------------------------------------------------------
# Tiendas online conectadas (self-service del comerciante)
# ---------------------------------------------------------------------------


def _store_frontend_redirect(params, shared=False):
    """Vuelve al frontend con el resultado de la instalación en la query
    string (``store_connected``, ``store_claim`` o ``store_error``). Nunca
    incluye el token de la tienda. ``shared``: quien vuelve instaló con un
    link para compartir y no tiene sesión nuestra, así que va a la página
    pública en vez de a la del comerciante."""
    base = str(settings.FRONTEND_URL).rstrip("/")
    if shared:
        path = str(getattr(settings, "STORE_SHARED_CONNECT_FRONTEND_PATH", "tienda_conectada.html")).lstrip("/")
    else:
        path = str(getattr(settings, "STORE_CONNECT_FRONTEND_PATH", "integraciones.html")).lstrip("/")
    query = f"?{urlencode(params)}" if params else ""
    return HttpResponseRedirect(f"{base}/{path}{query}")


class ShippingRateViewSet(viewsets.ModelViewSet):
    """ABM de la tabla de tarifas de las tiendas del usuario
    (``/api/v1/integrations/shipping-rates/``, permiso ``orders.create``).

    Es lo que contesta el checkout de esas tiendas, así que lo edita el
    dueño y nadie más: el queryset sale de ``request.user`` y el
    serializer revalida la tienda que manda el cliente.

    Filtro ``?store=<id>`` para editar una tienda por vez.
    """

    serializer_class = ShippingRateSerializer

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(STORE_CONNECT_PERMISSION)]

    def get_queryset(self):
        queryset = ShippingRate.objects.filter(
            connection__owner=self.request.user
        ).select_related("connection")
        store = str(self.request.query_params.get("store") or "").strip()
        if store.isdigit():
            queryset = queryset.filter(connection_id=int(store))
        return queryset

    def perform_create(self, serializer):
        instance = serializer.save()
        record(
            self.request,
            category="integrations",
            action="store.update",
            target=instance.connection,
            target_type="storeconnection",
            target_repr=str(instance.connection),
            changes={"tarifa": {"from": "", "to": str(instance)}},
        )
        shipping_rates.rates_changed(instance.connection)

    def perform_update(self, serializer):
        # Una tarifa puede cambiar de tienda: se republican las dos.
        previous_connection = serializer.instance.connection
        instance = serializer.save()
        shipping_rates.rates_changed(instance.connection)
        if previous_connection.pk != instance.connection_id:
            shipping_rates.rates_changed(previous_connection)

    def perform_destroy(self, instance):
        record(
            self.request,
            category="integrations",
            action="store.update",
            target=instance.connection,
            target_type="storeconnection",
            target_repr=str(instance.connection),
            changes={"tarifa": {"from": str(instance), "to": ""}},
        )
        connection = instance.connection
        instance.delete()
        shipping_rates.rates_changed(connection)


class StoreLabelRequestViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """GET /api/v1/integrations/store-labels/ — rótulos que las tiendas del
    usuario pidieron desde su propio admin (ver ``providers/tiendanube/labels.py``).

    Solo lectura: estos rótulos los crea y los resuelve la plataforma, no el
    comerciante. Existe para que pueda ver por qué NO le salió una etiqueta,
    en vez de que el motivo quede solo en el admin de Django.

    Filtros: ``?store=<id>`` y ``?status=pending,failed`` (varios separados
    por coma, igual criterio que la lista de pedidos).
    """

    serializer_class = StoreLabelRequestSerializer

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(STORE_CONNECT_PERMISSION)]

    def get_queryset(self):
        # La identidad sale del token, nunca de un id que manda el cliente:
        # solo los rótulos de tiendas cuyo dueño es quien consulta.
        queryset = StoreLabelRequest.objects.filter(
            connection__owner=self.request.user
        ).select_related("connection")

        store = str(self.request.query_params.get("store") or "").strip()
        if store.isdigit():
            queryset = queryset.filter(connection_id=int(store))

        statuses = [
            value.strip()
            for value in str(self.request.query_params.get("status") or "").split(",")
            if value.strip()
        ]
        valid = {choice for choice, _label in StoreLabelRequest.Status.choices}
        statuses = [value for value in statuses if value in valid]
        if statuses:
            queryset = queryset.filter(status__in=statuses)
        return queryset


class StoreConnectionViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """GET /api/v1/integrations/stores/ — tiendas conectadas del usuario.

    - ``POST /stores/claim/`` ``{"token": ...}``: vincula a la cuenta una
      tienda instalada desde la tienda de apps (ver ``stores.claim_store``).
    - ``POST /stores/<id>/disconnect/``: la desconecta (sin borrarla).
    - ``PATCH /stores/<id>/settings/``: cómo salen los rótulos de esa tienda
      (remitente, logo, plantilla preferida).
    """

    serializer_class = StoreConnectionSerializer

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(STORE_CONNECT_PERMISSION)]

    def get_queryset(self):
        return StoreConnection.objects.filter(owner=self.request.user)

    @action(detail=False, methods=["post"])
    def claim(self, request):
        serializer = StoreClaimSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            connection, claimed = claim_store(serializer.validated_data["token"], request.user)
        except StoreClaimError as exc:
            code = status.HTTP_409_CONFLICT if exc.conflict else status.HTTP_400_BAD_REQUEST
            return Response({"detail": str(exc)}, status=code)

        if claimed:
            record(
                request,
                category="integrations",
                action="store.claim",
                target=connection,
                target_type="storeconnection",
                target_repr=str(connection),
            )
        return Response(self.get_serializer(connection).data)

    @action(detail=True, methods=["patch"], url_path="settings")
    def store_settings(self, request, pk=None):
        """PATCH /api/v1/integrations/stores/<id>/settings/

        Cómo salen los rótulos de ESTA tienda: remitente
        (``sender_name``/``sender_address``/``sender_phone``), ``logo``,
        ``default_template`` y ``label_printer_dpmm`` (la densidad de su
        impresora térmica, ver apps.labels.zpl). Solo el dueño de la tienda:
        el queryset ya está recortado a ``request.user``."""
        connection = self.get_object()
        serializer = StoreSettingsSerializer(
            connection, data=request.data, partial=True, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)
        tracked = (
            "sender_name",
            "sender_address",
            "sender_phone",
            "logo",
            "default_template_id",
            "label_printer_dpmm",
        )
        previous = {field: getattr(connection, field) for field in tracked}
        connection = serializer.save()
        changes = {
            # El logo es un archivo: en la auditoría va su nombre, no el binario.
            field: {"from": str(previous[field] or ""), "to": str(getattr(connection, field) or "")}
            for field in tracked
            if previous[field] != getattr(connection, field)
        }
        if changes:
            record(
                request,
                category="integrations",
                action="store.update",
                target=connection,
                target_type="storeconnection",
                target_repr=str(connection),
                changes=changes,
            )
        return Response(self.get_serializer(connection).data)

    @action(detail=True, methods=["post"], url_path="check-print-plugin")
    def check_print_plugin(self, request, pk=None):
        """POST /api/v1/integrations/stores/<id>/check-print-plugin/

        Vincula ya el plugin de impresión que el comerciante acaba de instalar
        en su tienda, sin esperar el próximo repaso del worker. Devuelve la
        tienda con ``print_plugin_linked`` al día."""
        connection = self.get_object()
        if connection.status != StoreConnection.Status.ACTIVE:
            return Response({"detail": "La tienda no está conectada."}, status=status.HTTP_400_BAD_REQUEST)
        base_url = str(getattr(settings, "INTEGRATIONS_PUBLIC_BASE_URL", "") or "").rstrip("/")
        if not base_url:
            return Response(
                {"detail": "Falta la URL pública del servidor (INTEGRATIONS_PUBLIC_BASE_URL)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        get_provider(connection.platform).configure_admin_print(connection, base_url)
        connection.refresh_from_db()
        return Response(self.get_serializer(connection).data)

    @action(detail=True, methods=["post"], url_path="publish-rates")
    def publish_rates(self, request, pk=None):
        """POST /api/v1/integrations/stores/<id>/publish-rates/ ``{"enabled": bool}``

        Para las plataformas que cotizan con una tabla propia (VTEX): publica
        la tabla de tarifas en la tienda (y la mantiene al día con cada
        cambio) o la retira. Es lo que prende o apaga nuestro envío en SU
        checkout, así que lo decide el comerciante, como registrar el carrier
        en Tiendanube. La publicación la hace el worker; la respuesta trae la
        tienda con ``rates_push`` en ``pending``."""
        connection = self.get_object()
        if not get_provider(connection.platform).supports_rates_push:
            return Response(
                {"detail": f"En {connection.get_platform_display()} la tarifa se cotiza en el momento: no hay nada que publicar."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if connection.status != StoreConnection.Status.ACTIVE:
            return Response({"detail": "La tienda no está conectada."}, status=status.HTTP_400_BAD_REQUEST)
        enabled = request.data.get("enabled", True) if isinstance(request.data, dict) else True
        enabled = enabled if isinstance(enabled, bool) else str(enabled).lower() in ("1", "true", "yes")
        if enabled and not connection.shipping_rates.filter(is_active=True).exists():
            return Response(
                {"detail": "Cargá al menos una tarifa activa antes de publicar la tabla."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        previous = bool(shipping_rates.rates_push_state(connection).get("enabled"))
        shipping_rates.save_rates_push_state(connection, enabled=enabled)
        shipping_rates.enqueue_rates_push(connection)
        if previous != enabled:
            record(
                request,
                category="integrations",
                action="store.update",
                target=connection,
                target_type="storeconnection",
                target_repr=str(connection),
                changes={"tarifas_publicadas": {"from": previous, "to": enabled}},
            )
        return Response(self.get_serializer(connection).data)

    @action(detail=True, methods=["post"])
    def disconnect(self, request, pk=None):
        connection = self.get_object()
        if connection.status != StoreConnection.Status.REVOKED:
            disconnect_store(connection)
            record(
                request,
                category="integrations",
                action="store.disconnect",
                target=connection,
                target_type="storeconnection",
                target_repr=str(connection),
            )
        return Response(self.get_serializer(connection).data)


def _platform_label(platform):
    return StoreConnection.Platform(platform).label


class StoreInstallUrlView(APIView):
    """GET /api/v1/integrations/<plataforma>/install-url/[?shop=...]

    URL de autorización de la plataforma para el usuario logueado, con un
    ``state`` firmado que lo identifica al volver al callback. Las
    plataformas con dominio por tienda (Shopify) necesitan ``?shop=``: el
    state queda atado a esa tienda.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(STORE_CONNECT_PERMISSION)]

    def get(self, request, platform):
        provider = get_provider(platform)
        shop_domain = ""
        if provider.requires_shop_domain:
            try:
                shop_domain = provider.normalize_shop_domain(request.query_params.get("shop", ""))
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        try:
            url = provider.build_authorize_url(
                make_oauth_state(request.user, provider.platform, shop_domain), shop_domain=shop_domain
            )
        except ProviderError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({"authorize_url": url})


class StoreInstallShareLinkView(APIView):
    """POST /api/v1/integrations/<plataforma>/install-share-link/

    Link de instalación para que el comerciante se lo pase a quien
    administra la tienda (muchas veces no es la misma persona). Quien lo
    abre no necesita cuenta nuestra: autoriza en la plataforma y la tienda
    queda en la cuenta de quien generó el link. Solo existe en plataformas
    con authorization code y sin dominio por tienda (Tiendanube): las otras
    necesitan saber la tienda antes de autorizar.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(STORE_CONNECT_PERMISSION)]

    def post(self, request, platform):
        base = str(getattr(settings, "INTEGRATIONS_PUBLIC_BASE_URL", "")).rstrip("/")
        if not base:
            return Response(
                {"detail": "El servidor no tiene una dirección pública configurada para armar el link."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        token = make_share_token(request.user, platform)
        path = reverse(f"{platform}-install-share", kwargs={"token": token})
        max_age = getattr(settings, "INTEGRATIONS_INSTALL_SHARE_MAX_AGE_SECONDS", 259200)
        return Response({"share_url": f"{base}{path}", "expires_in_hours": max_age // 3600})


class StoreInstallShareView(APIView):
    """GET /api/v1/integrations/<plataforma>/install/<token>/

    Lo que abre quien recibió el link: público, sin sesión. Firma un
    ``state`` nuevo con el usuario del link (marcado como compartido) y lo
    manda a la autorización de la plataforma. El token del link vive días;
    el state, los minutos de siempre."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, platform, token):
        try:
            user = read_share_token(token, platform)
        except signing.BadSignature:
            return _store_frontend_redirect({"store_error": "share_link_invalid"}, shared=True)
        provider = get_provider(platform)
        try:
            url = provider.build_authorize_url(make_oauth_state(user, provider.platform, shared=True))
        except ProviderError:
            logger.exception("No se pudo iniciar la instalación compartida de %s", _platform_label(platform))
            return _store_frontend_redirect({"store_error": "provider_unavailable"}, shared=True)
        return HttpResponseRedirect(url)


class StoreOAuthCallbackView(APIView):
    """GET /api/v1/integrations/<plataforma>/callback/?code=...&state=...

    Es la *redirect URL* que se configura en el panel de la plataforma.
    Público (la plataforma redirige el navegador del comerciante, sin
    nuestro JWT): la identidad sale del ``state`` firmado o, si no nombra a
    nadie, del reclamo posterior. Shopify además firma el callback entero
    (``hmac``). Siempre termina redirigiendo al frontend, nunca muestra JSON.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, platform):
        provider = get_provider(platform)
        label = _platform_label(platform)
        state = request.query_params.get("state", "").strip()
        shared = oauth_state_is_shared(state)

        def redirect(params):
            return _store_frontend_redirect(params, shared=shared)

        if not provider.verify_callback(request.query_params):
            return redirect({"store_error": "invalid_signature"})

        code = request.query_params.get("code", "").strip()
        if not code:
            error = "authorization_cancelled" if request.query_params.get("error") else "missing_code"
            return redirect({"store_error": error})

        try:
            shop_domain = provider.callback_shop_domain(request.query_params)
        except ValueError:
            return redirect({"store_error": "invalid_shop"})

        if provider.requires_oauth_state and not state:
            return redirect({"store_error": "invalid_state"})
        try:
            user = read_oauth_state(state, provider.platform, shop_domain)
        except signing.BadSignature:
            return redirect({"store_error": "invalid_state"})

        try:
            result = connect_store(provider, code, user=user, shop_domain=shop_domain)
        except ProviderAuthError as exc:
            logger.warning("%s rechazó la instalación: %s", label, exc)
            return redirect({"store_error": "authorization_rejected"})
        except ProviderError:
            logger.exception("No se pudo completar la instalación de %s", label)
            return redirect({"store_error": "provider_unavailable"})

        connection = result.connection
        record(
            request,
            actor=user,
            category="integrations",
            action="store.connect",
            target=connection,
            target_type="storeconnection",
            target_repr=str(connection),
            changes={"status": {"from": None, "to": connection.status}},
        )

        if result.needs_claim:
            return redirect({"store_claim": make_claim_token(connection)})
        if result.owner_conflict:
            return redirect({"store_error": "owned_by_other_account"})
        return redirect({"store_connected": str(connection.pk)})


class StoreWebhookView(APIView):
    """POST /api/v1/integrations/<plataforma>/webhooks/

    Receptor de los webhooks de la plataforma. Todas exigen un 2xx en
    pocos segundos (Tiendanube 3, Shopify 5) y reintentan si no, así que
    acá solo se verifica la firma, se lee el aviso (``parse_webhook``: cada
    plataforma pone la tienda y el evento en un lugar distinto), se encola y
    se responde; el trabajo lo hace el worker (ver handlers).

    Un aviso de una tienda que no tenemos igual se encola (sin tienda) y
    queda como fallido, para poder depurarlo; responder error solo haría
    que la plataforma lo reintente.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, platform):
        provider = get_provider(platform)
        raw_body = request.body
        if provider.webhook_secret_per_store:
            # Firma con el secreto de CADA tienda (WooCommerce): primero hay
            # que saber de qué tienda es. ``?store=<id>`` va en la URL con la
            # que registramos el webhook; la firma prueba que es de ella.
            try:
                message = provider.parse_webhook(raw_body, request.headers)
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            if message is None:
                # El "ping" al crear el webhook: sin firma, no se encola nada.
                return Response({"received": False})
            connection = self._store_connection(provider, request.query_params.get("store"), message.store_id)
            if connection is None or not provider.verify_webhook(raw_body, request.headers, connection=connection):
                return Response({"detail": "Firma inválida."}, status=status.HTTP_401_UNAUTHORIZED)
        else:
            signed_by_app = provider.verify_webhook(raw_body, request.headers)
            try:
                message = provider.parse_webhook(raw_body, request.headers)
            except ValueError as exc:
                if not signed_by_app:
                    return Response({"detail": "Firma inválida."}, status=status.HTTP_401_UNAUTHORIZED)
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            connection = (
                StoreConnection.objects.filter(platform=provider.platform, external_store_id=message.store_id).first()
                if message is not None
                else None
            )
            # Sin la firma de nuestra app, todavía puede venir de la app
            # propia con la que el comerciante conectó esa tienda (Shopify
            # manual): la firma su secreto, guardado en la tienda.
            if not signed_by_app and not (
                connection is not None and provider.verify_store_webhook(raw_body, request.headers, connection)
            ):
                return Response({"detail": "Firma inválida."}, status=status.HTTP_401_UNAUTHORIZED)
            if message is None:
                return Response({"received": False})

        if message.event_type.startswith(INTERNAL_EVENT_PREFIX):
            # Los eventos internos solo los encola el backend.
            return Response({"received": False})
        enqueue_event(
            platform=provider.platform,
            event_type=message.event_type[:80],
            connection=connection,
            resource_id=message.resource_id[:100],
            payload=message.payload,
        )
        return Response({"received": True})

    @staticmethod
    def _store_connection(provider, store_param, external_store_id):
        """La tienda de un webhook firmado por tienda: por el id de la URL y,
        si no vino, por el origen que declara (``X-WC-Webhook-Source``)."""
        queryset = StoreConnection.objects.filter(platform=provider.platform)
        if str(store_param or "").isdigit():
            return queryset.filter(pk=int(store_param)).first()
        if external_store_id:
            return queryset.filter(external_store_id=external_store_id).first()
        return None


def _print_document(request, token, platform):
    """El PDF de un enlace de impresión firmado (``store_print``): lo comparten
    ``<plataforma>/print/<token>`` de Shopify, WooCommerce y Tiendanube."""
    if request.method != "GET":
        return HttpResponse(status=405)
    try:
        pdf_bytes, filename = store_print.render_document(token, platform)
    except store_print.PrintError as exc:
        return HttpResponse(str(exc), status=410, content_type="text/plain; charset=utf-8")
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    # Datos del comprador: que ningún proxy ni el navegador lo guarde.
    response["Cache-Control"] = "no-store"
    return response


def _record_store_connect(request, user, connection):
    record(
        request,
        actor=user,
        category="integrations",
        action="store.connect",
        target=connection,
        target_type="storeconnection",
        target_repr=str(connection),
        changes={"status": {"from": None, "to": connection.status}},
    )
