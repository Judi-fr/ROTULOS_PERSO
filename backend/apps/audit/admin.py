from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Solo lectura: el registro de auditoría es inmutable (ver models.py)."""

    list_display = ("created_at", "actor_email", "category", "action", "target_repr")
    list_filter = ("category", "action")
    search_fields = ("actor_email", "target_repr", "action")
    readonly_fields = [field.name for field in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
