"""Admin de la app processing."""

from django.contrib import admin

from .models import ImportacionRotulo


@admin.register(ImportacionRotulo)
class ImportacionRotuloAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "documento",
        "estado",
        "modelo",
        "confianza",
        "tokens_entrada",
        "tokens_salida",
        "creada_en",
    )
    list_filter = ("estado", "modelo", "creada_en")
    search_fields = ("documento__nombre_original", "request_id", "creada_por__email")
    # Una importación es el registro de algo que ya pasó: se consulta, no se
    # edita. Todo lo escribe el agente.
    readonly_fields = [f.name for f in ImportacionRotulo._meta.fields]

    def has_add_permission(self, request):
        # Crear una importación implica llamar al modelo y gastar tokens; eso
        # se hace por la API, que además registra quién la pidió.
        return False
