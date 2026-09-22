from django.contrib import admin

from .models import (
    IncomingWebhook,
    IntegrationEvent,
    IntegrationKey,
    StoreConnection,
    StoreLabelRequest,
    WebhookDelivery,
    WebhookEndpoint,
)


@admin.register(StoreConnection)
class StoreConnectionAdmin(admin.ModelAdmin):
    # El token (cifrado) nunca se muestra ni se edita desde el admin.
    list_display = ("platform", "name", "external_store_id", "owner", "status", "last_synced_at", "connected_at")
    list_filter = ("platform", "status")
    search_fields = ("name", "external_store_id", "owner__email")
    exclude = ("access_token_encrypted",)
    readonly_fields = ("connected_at", "disconnected_at", "last_synced_at", "created_at", "updated_at")


@admin.register(IntegrationEvent)
class IntegrationEventAdmin(admin.ModelAdmin):
    list_display = ("id", "platform", "event_type", "resource_id", "connection", "status", "attempts", "next_attempt_at")
    list_filter = ("platform", "status", "event_type")
    search_fields = ("resource_id", "connection__name", "connection__external_store_id")
    readonly_fields = [f.name for f in IntegrationEvent._meta.fields]

    def has_add_permission(self, request):
        # Solo se crean desde apps.integrations.events.enqueue_event.
        return False


@admin.register(IntegrationKey)
class IntegrationKeyAdmin(admin.ModelAdmin):
    # key_hash nunca se muestra en list_display/fields: ver el docstring
    # del modelo, no hay forma de "recuperar" la clave completa.
    list_display = ("name", "owner", "prefix", "is_active", "last_used_at", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "owner__email", "prefix")
    readonly_fields = ("key_hash", "prefix", "last_used_at", "created_at")


@admin.register(IncomingWebhook)
class IncomingWebhookAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "owner", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "owner__email")
    readonly_fields = ("secret", "created_at")


@admin.register(WebhookEndpoint)
class WebhookEndpointAdmin(admin.ModelAdmin):
    list_display = ("name", "url", "owner", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "url", "owner__email")
    readonly_fields = ("secret", "created_at", "updated_at")


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ("endpoint", "event", "status_code", "success", "attempt", "created_at")
    list_filter = ("success", "event")
    search_fields = ("endpoint__url", "endpoint__owner__email")
    readonly_fields = [f.name for f in WebhookDelivery._meta.fields]

    def has_add_permission(self, request):
        # Solo se crean desde apps.integrations.webhooks.send_webhook.
        return False


@admin.register(StoreLabelRequest)
class StoreLabelRequestAdmin(admin.ModelAdmin):
    """Solo lectura: acá se mira por qué un rótulo que pidió la tienda no
    salió. Lo crea el callback y lo resuelve el worker (ver store_labels)."""

    list_display = ("external_label_id", "connection", "status", "created_at", "released_at")
    list_filter = ("status", "connection__platform")
    search_fields = ("external_label_id", "external_fulfillment_order_id", "connection__name")
    readonly_fields = [f.name for f in StoreLabelRequest._meta.fields]

    def has_add_permission(self, request):
        return False
