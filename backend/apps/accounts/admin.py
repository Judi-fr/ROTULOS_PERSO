from django.contrib import admin
from django.contrib.auth.models import Group

from .models import EmailVerification, SupportMessage


def _ensure_groups():
    """Asegura que los grupos necesarios existan.

    Nombres en minúscula para que coincidan con los nombres de Group
    canónicos que usa el resto del sistema (ver ROLE_GROUP_MAP en
    serializers.py y los filtros de viewsets.py); en mayúscula quedarían
    como grupos "personalizados" distintos de los roles reales.
    """
    group_names = ["admin", "designer", "operator", "subscriber"]
    for name in group_names:
        Group.objects.get_or_create(name=name)


@admin.register(EmailVerification)
class EmailVerificationAdmin(admin.ModelAdmin):
    list_display = ("user", "is_verified", "expires_at", "verified_at")
    list_filter = ("is_verified",)
    search_fields = ("user__email", "token")


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    """Además de /admin/, hay una bandeja propia en el panel del admin
    (``/api/v1/support-messages/``, ver ``support_views.py``): esta pantalla
    de Django admin queda como respaldo/operación manual."""

    list_display = ("user", "subject", "status", "created_at", "responded_at")
    list_filter = ("status", "created_at")
    search_fields = ("user__email", "subject", "message")
    readonly_fields = ("user", "subject", "message", "created_at", "updated_at")