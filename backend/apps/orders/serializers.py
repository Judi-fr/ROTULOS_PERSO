"""Serializers de direcciones y pedidos del usuario autenticado."""

from rest_framework import serializers

from .models import Address, Order, OrderStatusEvent


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = [
            "id",
            "label",
            "street",
            "number",
            "city",
            "state",
            "postal_code",
            "country",
            "reference",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class OrderStatusEventSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = OrderStatusEvent
        fields = ["status", "status_label", "created_at"]


class OrderSerializer(serializers.ModelSerializer):
    """Lectura y creación de pedidos propios.

    El cliente elige una de sus direcciones y una descripción opcional; el
    estado y los datos de tracking los controla el backend (o, mientras no
    haya integración con el courier, el admin a mano) — no son campos que
    el cliente pueda escribir directamente. El único cambio de estado que
    el cliente puede disparar es cancelar (ver ``OrderViewSet.cancel``).
    """

    address = AddressSerializer(read_only=True)
    address_id = serializers.PrimaryKeyRelatedField(
        queryset=Address.objects.none(), source="address", write_only=True
    )
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    is_cancellable = serializers.BooleanField(read_only=True)
    status_events = OrderStatusEventSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "address",
            "address_id",
            "description",
            "status",
            "status_label",
            "is_cancellable",
            "carrier",
            "tracking_number",
            "tracking_url",
            "status_events",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "carrier",
            "tracking_number",
            "tracking_url",
            "created_at",
            "updated_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user and request.user.is_authenticated:
            # Un usuario solo puede asignar a un pedido una dirección PROPIA.
            self.fields["address_id"].queryset = Address.objects.filter(user=request.user)
