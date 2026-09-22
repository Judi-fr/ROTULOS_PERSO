from django.contrib import admin

from .models import (
    ElementLayout,
    Label,
    LabelSequence,
    LabelTemplate,
    LayoutElement,
    LayoutVariable,
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


@admin.register(LayoutVariable)
class LayoutVariableAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "data_type", "is_active", "is_system", "order")
    list_filter = ("is_active", "is_system", "data_type")
    search_fields = ("code", "label")


@admin.register(ElementLayout)
class ElementLayoutAdmin(admin.ModelAdmin):
    list_display = ("name", "width_mm", "height_mm", "dpi", "orientation", "is_active", "created_at")
    list_filter = ("orientation", "is_active")
    search_fields = ("name", "created_by__email")


@admin.register(LayoutElement)
class LayoutElementAdmin(admin.ModelAdmin):
    list_display = ("layout", "element_type", "variable", "order")
    list_filter = ("element_type",)
    search_fields = ("layout__name", "variable__label")
