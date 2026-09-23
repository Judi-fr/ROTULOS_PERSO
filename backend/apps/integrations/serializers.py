"""Serializers de integraciones (ABM de claves y webhooks, panel admin)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.labels.serializers import ImageOrDataUrlField

from . import store_labels
from .models import (
    IncomingWebhook,
    IntegrationKey,
    ShippingRate,
    StoreConnection,
    StoreLabelRequest,
    WebhookDelivery,
    WebhookEndpoint,
)

User = get_user_model()


class StoreConnectionSerializer(serializers.ModelSerializer):
    default_template_name = serializers.CharField(
        source="default_template.name", read_only=True, default=None
    )
    """Tienda conectada vista por su dueño. El token nunca se expone."""

    platform_label = serializers.CharField(source="get_platform_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    # ``null`` = no se sabe (tienda conectada antes de que se guardaran las
    # features del plan). El frontend no lo trata como un "no".
    label_api_enabled = serializers.SerializerMethodField()

    def get_label_api_enabled(self, connection):
        return store_labels.supports_label_api(connection)

    class Meta:
        model = StoreConnection
        fields = [
            "id",
            "platform",
            "platform_label",
            "external_store_id",
            "name",
            "store_url",
            "status",
            "status_label",
            "sender_name",
            "sender_address",
            "sender_phone",
            "logo",
            "default_template",
            "default_template_name",
            "scopes",
            "label_api_enabled",
            "last_error",
            "connected_at",
            "disconnected_at",
            "last_synced_at",
        ]
        read_only_fields = fields


class StoreSettingsSerializer(serializers.ModelSerializer):
    """Lo único que el comerciante edita de su tienda: cómo salen los
    rótulos de ESA tienda (remitente, logo, plantilla preferida). El resto
    (token, estado, scopes) lo maneja la plataforma, no el usuario.

    El logo acepta un archivo real (multipart) o el data URL base64 que ya
    produce el editor de rótulos, con el mismo tope de 2 MB
    (``ImageOrDataUrlField``); mandar ``null`` o "" lo borra."""

    logo = ImageOrDataUrlField(required=False, allow_null=True, field_label="Logo")

    class Meta:
        model = StoreConnection
        fields = ["sender_name", "sender_address", "sender_phone", "logo", "default_template"]

    def validate_default_template(self, template):
        # Misma regla que apps.labels: una plantilla propia o pública. El
        # dueño sale de request.user, nunca de un id que manda el cliente.
        if template is None:
            return template
        user = self.context["request"].user
        if not template.is_public and template.owner_id != user.pk:
            raise serializers.ValidationError("Esa plantilla no es tuya ni es pública.")
        return template


class StoreLabelRequestSerializer(serializers.ModelSerializer):
    """Un rótulo que pidió la tienda, visto por el comerciante.

    Solo el estado y el motivo del fallo: el ``payload`` guardado trae los
    datos del comprador y no sale de la base, y el token de descarga es de
    la plataforma, no del comerciante.
    """

    store_name = serializers.CharField(source="connection.name", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = StoreLabelRequest
        fields = [
            "id",
            "connection",
            "store_name",
            "external_label_id",
            "external_fulfillment_order_id",
            "status",
            "status_label",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ShippingRateSerializer(serializers.ModelSerializer):
    """Una fila de la tabla de tarifas de una tienda del usuario.

    ``connection`` se valida contra las tiendas de quien llama: el id lo
    manda el cliente, así que no alcanza con que exista.
    """

    store_name = serializers.CharField(source="connection.name", read_only=True)

    class Meta:
        model = ShippingRate
        fields = [
            "id",
            "connection",
            "store_name",
            "option_code",
            "option_name",
            "postal_code_from",
            "postal_code_to",
            "weight_up_to_kg",
            "price",
            "currency",
            "delivery_days_min",
            "delivery_days_max",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "store_name", "created_at", "updated_at"]

    def validate_connection(self, connection):
        if connection.owner_id != self.context["request"].user.pk:
            raise serializers.ValidationError("Esa tienda no es tuya.")
        return connection

    def validate(self, attrs):
        # Los CP se guardan normalizados para poder compararlos como texto
        # en la consulta del checkout (ver shipping_rates.matching_rates).
        from .shipping_rates import normalize_postal_code

        for field in ("postal_code_from", "postal_code_to"):
            if field in attrs:
                normalized = normalize_postal_code(attrs[field])
                if not normalized:
                    raise serializers.ValidationError(
                        {field: "Poné un código postal con números, por ejemplo 1602."}
                    )
                attrs[field] = normalized

        start = attrs.get("postal_code_from", getattr(self.instance, "postal_code_from", ""))
        end = attrs.get("postal_code_to", getattr(self.instance, "postal_code_to", ""))
        if start and end and start > end:
            raise serializers.ValidationError(
                {"postal_code_to": "El código postal final tiene que ser mayor o igual al inicial."}
            )

        price = attrs.get("price", getattr(self.instance, "price", None))
        if price is not None and price < 0:
            raise serializers.ValidationError({"price": "El precio no puede ser negativo."})

        days_min = attrs.get("delivery_days_min", getattr(self.instance, "delivery_days_min", None))
        days_max = attrs.get("delivery_days_max", getattr(self.instance, "delivery_days_max", None))
        if days_min is not None and days_max is not None and days_min > days_max:
            raise serializers.ValidationError(
                {"delivery_days_max": "El plazo máximo no puede ser menor que el mínimo."}
            )
        return attrs


class StoreClaimSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=500)


class IntegrationKeySerializer(serializers.ModelSerializer):
    owner_email = serializers.EmailField(source="owner.email", read_only=True)

    class Meta:
        model = IntegrationKey
        fields = [
            "id",
            "owner",
            "owner_email",
            "name",
            "prefix",
            "is_active",
            "last_used_at",
            "created_at",
        ]
        # key_hash nunca se expone (ver el docstring del modelo); prefix
        # solo se puede LEER, lo genera la vista al crear la clave.
        read_only_fields = ["id", "owner_email", "prefix", "last_used_at", "created_at"]


class IncomingWebhookSerializer(serializers.ModelSerializer):
    integration_key_name = serializers.CharField(source="integration_key.name", read_only=True)
    webhook_url = serializers.SerializerMethodField()

    class Meta:
        model = IncomingWebhook
        fields = [
            "id",
            "owner",
            "integration_key",
            "integration_key_name",
            "name",
            "slug",
            "secret",
            "webhook_url",
            "mapping",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "secret", "webhook_url", "created_at"]

    def get_webhook_url(self, obj):
        return f"/api/v1/ingest/webhooks/{obj.slug}/"


class WebhookEndpointSerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookEndpoint
        fields = [
            "id",
            "owner",
            "name",
            "url",
            "secret",
            "events",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "secret", "created_at", "updated_at"]


class WebhookDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookDelivery
        fields = [
            "id",
            "endpoint",
            "event",
            "attempt",
            "status_code",
            "success",
            "response_body",
            "created_at",
        ]
        read_only_fields = fields
