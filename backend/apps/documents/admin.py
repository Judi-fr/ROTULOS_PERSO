from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    # is_active: soft-delete, igual que Label/Order. No se borra de acá.
    list_display = ("name", "user", "kind", "status", "item_count", "is_active", "created_at")
    list_filter = ("kind", "status", "is_active")
    search_fields = ("name", "user__email")
