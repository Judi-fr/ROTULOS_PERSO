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

from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
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
from .models import IncomingWebhook, IntegrationKey, WebhookDelivery, WebhookEndpoint
from .serializers import (
    IncomingWebhookSerializer,
    IntegrationKeySerializer,
    WebhookDeliverySerializer,
    WebhookEndpointSerializer,
)
from .webhooks import dispatch_event, webhooks_suspended


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
