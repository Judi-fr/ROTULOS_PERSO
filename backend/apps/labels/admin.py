from django.contrib import admin

from .models import Label, LabelTemplate


@admin.register(LabelTemplate)
class LabelTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "is_public", "width_cm", "height_cm", "updated_at")
    list_filter = ("is_public",)
    search_fields = ("name", "owner__email")


@admin.register(Label)
class LabelAdmin(admin.ModelAdmin):
    # is_active: soft-delete, igual que Order/User. No se borra de acá.
    list_display = ("name", "user", "client", "order", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "client", "user__email")
