from django.contrib import admin

from .models import Document, UploadedLabelFile


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    # is_active: soft-delete, igual que Label/Order. No se borra de acá.
    list_display = ("name", "user", "kind", "status", "item_count", "is_active", "created_at")
    list_filter = ("kind", "status", "is_active")
    search_fields = ("name", "user__email")


@admin.register(UploadedLabelFile)
class UploadedLabelFileAdmin(admin.ModelAdmin):
    # Archivo fuente subido para importar.
    list_display = ("original_filename", "mime_type", "size_bytes", "uploaded_by", "uploaded_at")
    list_filter = ("mime_type",)
    search_fields = ("original_filename", "uploaded_by__email")
