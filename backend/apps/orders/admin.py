from django.contrib import admin

from .models import Address, Order, OrderStatusEvent


class OrderStatusEventInline(admin.TabularInline):
    model = OrderStatusEvent
    extra = 0
    readonly_fields = ("status", "created_at")
    can_delete = False


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("label", "user", "city", "is_default", "created_at")
    list_filter = ("is_default", "country")
    search_fields = ("user__email", "label", "street", "city")


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    # El estado se cambia acá mientras no exista la integración con el
    # courier: al guardar, Order.save() registra el evento en el timeline.
    list_display = ("id", "user", "address", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("user__email", "description", "tracking_number")
    inlines = [OrderStatusEventInline]
