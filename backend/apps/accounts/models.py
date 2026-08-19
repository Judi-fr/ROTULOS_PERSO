from __future__ import annotations

import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

User = get_user_model()


class EmailVerification(models.Model):
    """Estado de verificación de email para un usuario."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="email_verification")
    token = models.CharField(max_length=255, unique=True, blank=True, default="")
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "verificación de email"
        verbose_name_plural = "verificaciones de email"

    def is_expired(self):
        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at

    def refresh_token(self):
        self.token = secrets.token_urlsafe(32)
        self.is_verified = False
        self.verified_at = None
        self.expires_at = timezone.now() + timedelta(days=1)
        self.save(update_fields=["token", "is_verified", "verified_at", "expires_at", "updated_at"])
        return self.token

    def __str__(self):
        state = "verificado" if self.is_verified else "pendiente"
        return f"{self.user.email} ({state})"
