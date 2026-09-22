"""Serializers de direcciones y pedidos del usuario autenticado."""

from rest_framework import serializers

from .models import Address, ImportMapping, Order, OrderImport, OrderStatusEvent


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = [
            "id",
            "label",
            "recipient_name",
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
    is_shippable = serializers.BooleanField(read_only=True)
    status_events = OrderStatusEventSerializer(many=True, read_only=True)
    store_platform = serializers.CharField(source="store_connection.platform", read_only=True, default=None)
    store_name = serializers.CharField(source="store_connection.name", read_only=True, default=None)

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
            "is_shippable",
            "carrier",
            "tracking_number",
            "tracking_url",
            "external_id",
            "source",
            "store_connection",
            "store_platform",
            "store_name",
            "external_number",
            "contact_email",
            "contact_phone",
            "shipping_option",
            "package_count",
            "total_weight_kg",
            "items",
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
            "external_id",
            "source",
            "store_connection",
            "external_number",
            "contact_email",
            "contact_phone",
            "shipping_option",
            "package_count",
            "total_weight_kg",
            "items",
            "created_at",
            "updated_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user and request.user.is_authenticated:
            # Un usuario solo puede asignar a un pedido una dirección PROPIA.
            self.fields["address_id"].queryset = Address.objects.filter(user=request.user)


class OrderShipSerializer(serializers.Serializer):
    """Despacho / seguimiento de un pedido propio (``OrderViewSet.ship``).

    El estado solo avanza (``Order.STATUS_PROGRESS``); repetir el estado
    actual sirve para cargar o corregir el tracking sin cambiarlo.
    Transportista y tracking se actualizan solo si vienen en el body.
    """

    status = serializers.ChoiceField(
        choices=[
            (Order.Status.DISPATCHED, Order.Status.DISPATCHED.label),
            (Order.Status.IN_TRANSIT, Order.Status.IN_TRANSIT.label),
            (Order.Status.DELIVERED, Order.Status.DELIVERED.label),
        ]
    )
    carrier = serializers.CharField(max_length=100, required=False, allow_blank=True)
    tracking_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    tracking_url = serializers.URLField(max_length=200, required=False, allow_blank=True)

    def validate(self, attrs):
        order = self.context["order"]
        if not order.is_shippable:
            raise serializers.ValidationError(
                {"status": f"El pedido está «{order.get_status_display()}»: ya no se puede actualizar su envío."}
            )
        if Order.status_rank(attrs["status"]) < Order.status_rank(order.status):
            raise serializers.ValidationError(
                {"status": f"El pedido ya está «{order.get_status_display()}»: el estado del envío solo puede avanzar."}
            )
        return attrs


class AdminOrderSerializer(serializers.ModelSerializer):
    """Lectura de pedidos de TODOS los usuarios para el panel admin
    (``orders.view_all``). Solo lectura: el admin no crea ni cancela
    pedidos ajenos desde acá (ver ``AdminOrderListView``)."""

    user_email = serializers.EmailField(source="user.email", read_only=True)
    address = AddressSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    last_event = serializers.SerializerMethodField()
    store_platform = serializers.CharField(source="store_connection.platform", read_only=True, default=None)
    store_name = serializers.CharField(source="store_connection.name", read_only=True, default=None)

    class Meta:
        model = Order
        fields = [
            "id",
            "user",
            "user_email",
            "address",
            "description",
            "status",
            "status_label",
            "carrier",
            "tracking_number",
            "tracking_url",
            "external_id",
            "source",
            "store_connection",
            "store_platform",
            "store_name",
            "external_number",
            "contact_email",
            "contact_phone",
            "shipping_option",
            "package_count",
            "total_weight_kg",
            "items",
            "last_event",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_last_event(self, obj):
        # ``status_events`` viene prefetched y ordenado ascendente (Meta.ordering
        # de OrderStatusEvent): el último elemento es el evento más reciente,
        # sin disparar una query extra por pedido.
        events = list(obj.status_events.all())
        if not events:
            return None
        last = events[-1]
        return {
            "status": last.status,
            "status_label": last.get_status_display(),
            "created_at": last.created_at,
        }


class OrderImportSerializer(serializers.ModelSerializer):
    """Solo lectura: un ``OrderImport`` lo crea/completa el flujo de subir
    -> mapear -> confirmar (``import_views``), acá solo se expone su
    estado y contadores."""

    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = OrderImport
        fields = [
            "id",
            "original_filename",
            "status",
            "status_label",
            "total_rows",
            "imported_count",
            "skipped_count",
            "error_count",
            "errors",
            "mapping",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ImportMappingSerializer(serializers.ModelSerializer):
    """CRUD de las plantillas de mapeo propias del usuario (story 22)."""

    class Meta:
        model = ImportMapping
        fields = ["id", "name", "mapping", "created_at"]
        read_only_fields = ["id", "created_at"]
