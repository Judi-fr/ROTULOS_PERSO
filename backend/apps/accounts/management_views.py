"""CRUD de usuarios para el administrador.

Reservado a usuarios con ``is_staff`` (permiso ``IsAdminUser``). Expone las
cinco operaciones estándar sobre el modelo User bajo ``/api/v1/users/``:

    GET    /api/v1/users/         -> listar (paginado)
    POST   /api/v1/users/         -> crear
    GET    /api/v1/users/<id>/    -> detalle
    PATCH  /api/v1/users/<id>/    -> editar (parcial)
    PUT    /api/v1/users/<id>/    -> editar (completo)
    DELETE /api/v1/users/<id>/    -> eliminar
"""

from django.contrib.auth import get_user_model
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAdminUser

from .serializers import UserAdminSerializer

User = get_user_model()


class UserAdminViewSet(viewsets.ModelViewSet):
    """CRUD de usuarios reservado a administradores (``is_staff``).

    Dos guardas evitan que un admin se deje a sí mismo fuera del sistema:
    no puede eliminar su propia cuenta ni quitarse su propio ``is_staff``.
    """

    queryset = User.objects.all().order_by("id")
    serializer_class = UserAdminSerializer
    permission_classes = [IsAdminUser]

    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.pk == self.request.user.pk:
            # ``is_staff`` puede no venir en el body (PATCH): si no viene, se
            # conserva el actual, así que solo bloqueamos cuando llega en False.
            nuevo_is_staff = serializer.validated_data.get("is_staff", instance.is_staff)
            if not nuevo_is_staff:
                raise PermissionDenied(
                    "No podés quitarte tu propio acceso de administrador."
                )
        serializer.save()

    def perform_destroy(self, instance):
        if instance.pk == self.request.user.pk:
            raise PermissionDenied("No podés eliminar tu propia cuenta de administrador.")
        instance.delete()
