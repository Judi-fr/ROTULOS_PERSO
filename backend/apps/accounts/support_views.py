"""Form de contacto ("Ayuda/Soporte") del dashboard del usuario.

Sin integración de email real todavía: el mensaje se persiste en
``SupportMessage`` y lo lee un admin desde /admin/ (ver ``admin.py``). Mismo
patrón self-service que ``apps.orders`` (identidad siempre de
``request.user``, permiso atómico vía ``HasRolePermission``).

    POST /api/v1/auth/support/  -> crea un mensaje de soporte propio
"""

from rest_framework import generics, serializers
from rest_framework.permissions import IsAuthenticated

from .models import SupportMessage
from .role_permissions import HasRolePermission


class SupportMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportMessage
        fields = ["id", "subject", "message", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_subject(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("El asunto no puede estar vacío.")
        return value

    def validate_message(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("El mensaje no puede estar vacío.")
        return value


class SupportMessageView(generics.CreateAPIView):
    """POST-only: el dashboard solo necesita guardar el mensaje, no listarlo."""

    serializer_class = SupportMessageSerializer

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("support.create")]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
