"""Registro de los modelos de labels en el admin de Django.

Los elementos se editan en línea dentro de la plantilla (``TabularInline``)
porque no tienen sentido por sí solos: siempre pertenecen a una plantilla. El
catálogo de variables, en cambio, se administra aparte: es compartido y vive
con independencia de cualquier plantilla.
"""

from django.contrib import admin

from .models import ElementoPlantilla, Plantilla, VariableRotulo


@admin.register(VariableRotulo)
class VariableRotuloAdmin(admin.ModelAdmin):
    list_display = (
        "etiqueta",
        "codigo",
        "tipo_dato",
        "activa",
        "es_sistema",
        "orden",
    )
    list_filter = ("tipo_dato", "activa", "es_sistema")
    list_editable = ("activa", "orden")
    search_fields = ("codigo", "etiqueta", "descripcion")
    readonly_fields = ("es_sistema", "creada_por", "creada_en")

    def has_delete_permission(self, request, obj=None):
        # Mismo criterio que la API: las variables del sistema no se borran.
        # Para retirarlas de circulación está el flag ``activa``.
        if obj is not None and obj.es_sistema:
            return False
        return super().has_delete_permission(request, obj)


class ElementoPlantillaInline(admin.TabularInline):
    model = ElementoPlantilla
    extra = 0
    fields = (
        "orden",
        "tipo",
        "variable",
        "contenido",
        "x_mm",
        "y_mm",
        "ancho_mm",
        "alto_mm",
        "estilo",
    )


@admin.register(Plantilla)
class PlantillaAdmin(admin.ModelAdmin):
    list_display = (
        "nombre",
        "ancho_mm",
        "alto_mm",
        "dpi",
        "orientacion",
        "activa",
        "creada_en",
    )
    list_filter = ("activa", "orientacion", "dpi")
    search_fields = ("nombre", "descripcion")
    inlines = [ElementoPlantillaInline]

    def get_queryset(self, request):
        # El inline muestra la variable de cada elemento; sin esto, la ficha
        # de una plantilla dispara una consulta por elemento.
        return super().get_queryset(request).prefetch_related("elementos__variable")
