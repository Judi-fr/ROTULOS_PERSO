from django.contrib import admin
from django.contrib.auth.models import Group

from .models import EmailVerification, SupportMessage


def _ensure_groups():
    """Asegura que los grupos necesarios existan."""
    group_names = ["Admin", "Designer", "Operator", "Subscriber"]
    for name in group_names:
        Group.objects.get_or_create(name=name)


class EmailVerificationAdmin(admin.ModelAdmin):
    list_display = ("user", "is_verified", "expires_at", "verified_at")
    list_filter = ("is_verified",)
    search_fields = ("user__email", "token")


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    """Sin canal de email real todavía, /admin/ es la única forma de leer
    los mensajes del form de "Ayuda/Soporte" del dashboard."""

    list_display = ("user", "subject", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user__email", "subject", "message")
    readonly_fields = ("user", "subject", "message", "created_at")