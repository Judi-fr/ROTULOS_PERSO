from django.contrib import admin

from .models import (
    ElementoPlantilla,
    Label,
    LabelSequence,
    LabelTemplate,
    Plantilla,
    VariableRotulo,
)


@admin.register(LabelTemplate)
class LabelTemplateAdmin(admin.ModelAdmin):
    # is_active: soft-delete (archivar), igual que Label/Document.
    list_display = ("name", "owner", "is_public", "is_active", "width_cm", "height_cm", "updated_at")
    list_filter = ("is_public", "is_active")
    search_fields = ("name", "owner__email")


@admin.register(Label)
class LabelAdmin(admin.ModelAdmin):
    # is_active: soft-delete, igual que Order/User. No se borra de acá.
    list_display = ("name", "user", "client", "order", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "client", "user__email")


@admin.register(LabelSequence)
class LabelSequenceAdmin(admin.ModelAdmin):
    # {{secuencia}} del render (Historia 29): consulta/ajuste manual del
    # contador, sin endpoint CRUD propio todavía.
    list_display = ("owner", "key", "prefix", "padding", "current", "updated_at")
    search_fields = ("owner__email", "key")


@admin.register(VariableRotulo)
class VariableRotuloAdmin(admin.ModelAdmin):
    list_display = ("codigo", "etiqueta", "tipo_dato", "activa", "es_sistema", "orden")
    list_filter = ("activa", "es_sistema", "tipo_dato")
    search_fields = ("codigo", "etiqueta")


@admin.register(Plantilla)
class PlantillaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "ancho_mm", "alto_mm", "dpi", "orientacion", "activa", "creada_en")
    list_filter = ("orientacion", "activa")
    search_fields = ("nombre", "creada_por__email")


@admin.register(ElementoPlantilla)
class ElementoPlantillaAdmin(admin.ModelAdmin):
    list_display = ("plantilla", "tipo", "variable", "orden")
    list_filter = ("tipo",)
    search_fields = ("plantilla__nombre", "variable__etiqueta")
