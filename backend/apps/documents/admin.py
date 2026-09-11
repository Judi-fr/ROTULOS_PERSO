from django.contrib import admin

from .models import Document, Documento


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    # is_active: soft-delete, igual que Label/Order. No se borra de acá.
    list_display = ("name", "user", "kind", "status", "item_count", "is_active", "created_at")
    list_filter = ("kind", "status", "is_active")
    search_fields = ("name", "user__email")


@admin.register(Documento)
class DocumentoAdmin(admin.ModelAdmin):
    # Archivo fuente subido para importar (integración desde backend_echu).
    list_display = ("nombre_original", "tipo_mime", "tamano_bytes", "subido_por", "subido_en")
    list_filter = ("tipo_mime",)
    search_fields = ("nombre_original", "subido_por__email")
