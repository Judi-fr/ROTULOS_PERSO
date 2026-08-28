"""Direcciones y pedidos del usuario autenticado (self-service).

Igual que ``ProfileView``: ninguna vista recibe un id de OTRO usuario por
parámetro para filtrar; el queryset siempre se recorta a ``request.user``,
así que un cliente nunca puede ver, editar ni cancelar direcciones o
pedidos ajenos.
"""

from django.db.models import ProtectedError
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.role_permissions import HasRolePermission

from .models import Address, Order
from .serializers import AddressSerializer, OrderSerializer


class AddressViewSet(viewsets.ModelViewSet):
    """CRUD de las direcciones propias del usuario autenticado."""

    serializer_class = AddressSerializer

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("addresses.manage")]

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            return Response(
                {"detail": "No podés eliminar esta dirección: tiene pedidos asociados."},
                status=status.HTTP_400_BAD_REQUEST,
            )


class OrderViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Alta, lectura y cancelación de pedidos propios.

    No expone update/destroy genéricos: el cliente no edita un pedido ya
    creado ni lo borra, solo puede cancelarlo (mientras siga en un estado
    cancelable) a través de la acción ``cancel``.
    """

    serializer_class = OrderSerializer

    def get_permissions(self):
        if self.action == "create":
            permission = "orders.create"
        elif self.action == "cancel":
            permission = "orders.cancel"
        else:
            permission = "orders.view"
        return [IsAuthenticated(), HasRolePermission(permission)]

    def get_queryset(self):
        return (
            Order.objects.filter(user=self.request.user)
            .select_related("address")
            .prefetch_related("status_events")
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        if not order.is_cancellable:
            return Response(
                {"detail": "Este pedido ya no se puede cancelar."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        order.status = Order.Status.CANCELLED
        order.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(order).data)
