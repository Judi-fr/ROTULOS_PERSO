from django.contrib import admin

from .models import Address, ImportMapping, Order, OrderImport, OrderStatusEvent


class OrderStatusEventInline(admin.TabularInline):
    model = OrderStatusEvent
    extra = 0
    readonly_fields = ("status", "created_at")
    can_delete = False


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("label", "recipient_name", "user", "city", "is_default", "created_at")
    list_filter = ("is_default", "country")
    search_fields = ("user__email", "label", "recipient_name", "street", "city")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    # El estado se cambia acá mientras no exista la integración con el
    # courier: al guardar, Order.save() registra el evento en el timeline.
    list_display = ("id", "user", "address", "status", "source", "external_id", "created_at")
    list_filter = ("status", "source")
    search_fields = ("user__email", "description", "tracking_number", "external_id")
    inlines = [OrderStatusEventInline]


@admin.register(OrderImport)
class OrderImportAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "original_filename", "status", "imported_count", "skipped_count", "error_count", "created_at")
    list_filter = ("status",)
    search_fields = ("user__email", "original_filename")
    readonly_fields = ("errors", "mapping", "created_at", "updated_at")


@admin.register(ImportMapping)
class ImportMappingAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "created_at")
    search_fields = ("name", "user__email")
