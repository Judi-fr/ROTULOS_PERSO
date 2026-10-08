from django.contrib import admin

from .models import CarrierAccount, CarrierShipment, CarrierShipmentEvent


@admin.register(CarrierAccount)
class CarrierAccountAdmin(admin.ModelAdmin):
    list_display = ("carrier", "owner", "environment", "is_active", "updated_at")
    list_filter = ("carrier", "environment", "is_active")
    search_fields = ("owner__email", "username", "client_code")
    # Las credenciales cifradas no se muestran ni se editan desde el admin.
    exclude = ("password_encrypted", "token_encrypted")


class CarrierShipmentEventInline(admin.TabularInline):
    model = CarrierShipmentEvent
    extra = 0
    can_delete = False
    readonly_fields = ("occurred_at", "cycle", "event", "reason", "sub_reason", "status_text", "branch", "comment")


@admin.register(CarrierShipment)
class CarrierShipmentAdmin(admin.ModelAdmin):
    list_display = ("tracking_number", "carrier", "order", "status", "carrier_status", "last_checked_at", "created_at")
    list_filter = ("carrier", "status")
    search_fields = ("tracking_number", "order__external_number")
    raw_id_fields = ("order", "account")
    inlines = [CarrierShipmentEventInline]
