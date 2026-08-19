from django.contrib import admin
from django.contrib.auth.models import Group

from .models import EmailVerification


def _ensure_groups():
    """Asegura que los grupos necesarios existan."""
    group_names = ["Admin", "Designer", "Operator", "Subscriber"]
    for name in group_names:
        Group.objects.get_or_create(name=name)


class EmailVerificationAdmin(admin.ModelAdmin):
    list_display = ("user", "is_verified", "expires_at", "verified_at")
    list_filter = ("is_verified",)
    search_fields = ("user__email", "token")