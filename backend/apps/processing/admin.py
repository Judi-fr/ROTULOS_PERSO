from django.contrib import admin

from .models import ImportacionRotulo


@admin.register(ImportacionRotulo)
class ImportacionRotuloAdmin(admin.ModelAdmin):
    list_display = (
        "documento",
        "estado",
        "modelo",
        "confianza",
        "tokens_entrada",
        "tokens_salida",
        "creada_por",
        "creada_en",
    )
    list_filter = ("estado", "modelo")
    search_fields = ("creada_por__email", "documento__nombre_original")
