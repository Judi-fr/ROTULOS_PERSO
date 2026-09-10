from django.contrib import admin

from .models import IncomingWebhook, IntegrationKey, WebhookDelivery, WebhookEndpoint


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
