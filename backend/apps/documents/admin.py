"""Admin de la app documents."""

from django.contrib import admin

from .models import Documento


@admin.register(Documento)
class DocumentoAdmin(admin.ModelAdmin):
    list_display = ("nombre_original", "tipo_mime", "tamano_kb", "subido_por", "subido_en")
    list_filter = ("tipo_mime", "subido_en")
    search_fields = ("nombre_original", "subido_por__email")
    # Los metadatos se derivan del contenido del archivo al subirlo; editarlos
    # a mano solo lograría que dejen de describir lo que realmente se guardó.
    readonly_fields = ("nombre_original", "tipo_mime", "tamano_bytes", "subido_en")

    @admin.display(description="tamaño")
    def tamano_kb(self, obj):
        return f"{obj.tamano_bytes / 1024:.0f} KB"
