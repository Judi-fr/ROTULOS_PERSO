"""Serializers de integraciones (ABM de claves y webhooks, panel admin)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import IncomingWebhook, IntegrationKey, WebhookDelivery, WebhookEndpoint

User = get_user_model()


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
