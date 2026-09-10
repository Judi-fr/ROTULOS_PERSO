"""Direcciones y pedidos del usuario autenticado (self-service).

Igual que ``ProfileView``: ninguna vista recibe un id de OTRO usuario por
parámetro para filtrar; el queryset siempre se recorta a ``request.user``,
así que un cliente nunca puede ver, editar ni cancelar direcciones o
pedidos ajenos.
"""

from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.db.models import Count, Max, ProtectedError, Q
from django.db.models.functions import TruncMonth
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.pagination import UserAdminPagination
from apps.accounts.permissions_map import user_has_permission
from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record

from .ingestion import TARGET_FIELDS, create_order_from_data, validate_mapped_row
from .models import Address, Order
from .serializers import AddressSerializer, AdminOrderSerializer, OrderSerializer

User = get_user_model()


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
        order = serializer.save(user=self.request.user)
        record(
            self.request,
            category="orders",
            action="order.create",
            target=order,
            target_type="order",
            target_repr=str(order),
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        order = self.get_object()
        if not order.is_cancellable:
            return Response(
                {"detail": "Este pedido ya no se puede cancelar."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        previous_status = order.status
        # El save() del modelo ya deja su propio rastro de auditoría
        # (order.status_change, sin actor) para ediciones hechas desde el
        # admin de Django o a mano; acá se evita duplicarlo porque esta
        # cancelación SÍ tiene un actor conocido (order.cancel, más abajo).
        order._skip_status_audit = True
        order.status = Order.Status.CANCELLED
        order.save(update_fields=["status", "updated_at"])
        record(
            request,
            category="orders",
            action="order.cancel",
            target=order,
            target_type="order",
            target_repr=str(order),
            changes={"status": {"from": previous_status, "to": order.status}},
        )
        return Response(self.get_serializer(order).data)


class ManualOrderCreateView(APIView):
    """POST /api/v1/orders/manual/

    Carga OPERATIVA de un envío (story 20): alguien de Buspack escribe los
    datos del destinatario a mano —no está guardado en ninguna dirección
    propia, a diferencia del alta self-service de ``OrderViewSet.create``,
    que elige entre las direcciones YA guardadas del propio cliente—.
    Crea la ``Address`` y el ``Order`` en una sola llamada (ver
    ``ingestion.create_order_from_data``).

    Campos: los de ``ingestion.TARGET_FIELDS`` (destinatario, domicilio,
    numero, ciudad, provincia, cp, referencia, descripcion, external_id).
    Con ``user_id`` Y el permiso ``orders.create_for_others`` (solo
    admin), da de alta el envío a nombre de OTRO usuario; sin ese permiso
    el pedido es siempre del usuario autenticado, ``user_id`` se ignora
    aunque venga en el body — la identidad nunca sale de un id que manda
    el cliente.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("orders.create_manual")]

    def post(self, request):
        data = request.data
        target_user = request.user

        user_id = data.get("user_id")
        if user_id:
            if not user_has_permission(request.user, "orders.create_for_others"):
                raise PermissionDenied(
                    "No tenés permiso para dar de alta pedidos a nombre de otro usuario."
                )
            target_user = get_object_or_404(User, pk=user_id)

        mapped = {field: data.get(field) for field in TARGET_FIELDS}
        row_errors = validate_mapped_row(mapped)
        if row_errors:
            raise ValidationError({"detail": row_errors})

        order, created = create_order_from_data(target_user, mapped, source=Order.Source.MANUAL)
        record(
            request,
            category="orders",
            action="order.create",
            target=order,
            target_type="order",
            target_repr=str(order),
            changes={"source": {"from": None, "to": Order.Source.MANUAL}} if created else None,
        )
        return Response(
            OrderSerializer(order, context={"request": request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AdminOrderPagination(UserAdminPagination):
    page_size = 20


class AdminOrderListView(ListAPIView):
    """GET /api/v1/admin/orders/

    Pedidos de TODOS los usuarios para el panel admin (``orders.view_all``).
    Solo lectura: el queryset de ``OrderViewSet`` (self-service) no cambia,
    este es un endpoint aparte con su propio permiso.
    """

    serializer_class = AdminOrderSerializer
    pagination_class = AdminOrderPagination

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("orders.view_all")]

    def _parse_date_param(self, param_name):
        raw = self.request.query_params.get(param_name, "").strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError(
                {param_name: f"Formato de fecha inválido (usar YYYY-MM-DD): {raw!r}."}
            )

    def get_queryset(self):
        queryset = (
            Order.objects.select_related("user", "address")
            .prefetch_related("status_events")
            .all()
        )
        params = self.request.query_params

        search = params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(user__email__icontains=search) | Q(description__icontains=search)
            )

        status_param = params.get("status", "").strip()
        if status_param:
            queryset = queryset.filter(status=status_param)

        user_param = params.get("user", "").strip()
        if user_param:
            if user_param.isdigit():
                queryset = queryset.filter(user_id=int(user_param))
            else:
                queryset = queryset.filter(user__email__icontains=user_param)

        date_from = self._parse_date_param("date_from")
        date_to = self._parse_date_param("date_to")
        if date_from and date_to and date_from > date_to:
            raise ValidationError(
                {"date_to": "'date_to' no puede ser anterior a 'date_from'."}
            )
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        return queryset.order_by("-created_at")


class OrderMetricsView(APIView):
    """GET /api/v1/orders/metrics/?months=6

    Métricas de pedidos para el panel admin (``orders.view_all``). Todas las
    métricas se calculan sobre pedidos CREADOS dentro de la ventana de
    ``months`` (mismo criterio que ``months`` en ``users/metrics/``), para
    que el selector de período del frontend afecte a todo el bloque por
    igual.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("orders.view_all")]

    def _months(self, request):
        raw_months = request.query_params.get("months", "6").strip()
        try:
            months = int(raw_months)
        except ValueError:
            raise ValidationError({"months": f"'months' debe ser un número entero: {raw_months!r}."})
        return max(1, min(months, 12))

    def get(self, request):
        months = self._months(request)
        cutoff = (timezone.now() - timedelta(days=30 * months)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        base_qs = Order.objects.filter(created_at__gte=cutoff)

        orders_by_month = [
            {"month": row["month"].strftime("%Y-%m"), "count": row["count"]}
            for row in base_qs.annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(count=Count("id"))
            .order_by("month")
            if row["month"] is not None
        ]

        status_counts = dict(
            base_qs.values_list("status").annotate(count=Count("id")).values_list("status", "count")
        )
        funnel_statuses = [
            Order.Status.CREATED,
            Order.Status.PREPARING,
            Order.Status.DISPATCHED,
            Order.Status.IN_TRANSIT,
            Order.Status.DELIVERED,
        ]
        orders_by_status = {
            "funnel": [
                {"status": s.value, "label": s.label, "count": status_counts.get(s.value, 0)}
                for s in funnel_statuses
            ],
            "cancelled": status_counts.get(Order.Status.CANCELLED, 0),
        }

        total_orders = base_qs.count()
        cancelled_qs = base_qs.filter(status=Order.Status.CANCELLED).prefetch_related("status_events")
        cancelled_count = cancelled_qs.count()
        prior_status_counts = {}
        for order in cancelled_qs:
            # El timeline viene ascendente (Meta.ordering de OrderStatusEvent):
            # el último evento es la propia cancelación, el anterior es el
            # estado en el que estaba justo antes de cancelarse.
            events = list(order.status_events.all())
            prior = events[-2].status if len(events) >= 2 else order.status
            prior_status_counts[prior] = prior_status_counts.get(prior, 0) + 1
        cancellation_rate = {
            "rate": round(cancelled_count * 100 / total_orders, 1) if total_orders else 0.0,
            "cancelled_count": cancelled_count,
            "total_orders": total_orders,
            "prior_status_breakdown": [
                {"status": key, "label": Order.Status(key).label, "count": count}
                for key, count in sorted(prior_status_counts.items(), key=lambda item: -item[1])
            ],
        }

        transition_totals = {}
        for order in base_qs.prefetch_related("status_events"):
            events = list(order.status_events.all())
            for previous_event, current_event in zip(events, events[1:]):
                key = f"{previous_event.status}→{current_event.status}"
                delta_hours = (current_event.created_at - previous_event.created_at).total_seconds() / 3600
                bucket = transition_totals.setdefault(key, {"total_hours": 0.0, "count": 0})
                bucket["total_hours"] += delta_hours
                bucket["count"] += 1
        avg_time_between_statuses = [
            {
                "transition": key,
                "average_hours": round(data["total_hours"] / data["count"], 1),
                "sample_size": data["count"],
            }
            for key, data in sorted(transition_totals.items())
        ]

        top_users_qs = (
            base_qs.values("user__email")
            .annotate(count=Count("id"), last_order_at=Max("created_at"))
            .order_by("-count")[:10]
        )
        top_users_by_orders = [
            {"email": row["user__email"], "count": row["count"], "last_order_at": row["last_order_at"]}
            for row in top_users_qs
        ]

        total_users = User.objects.count()
        users_with_orders_count = base_qs.values("user_id").distinct().count()
        users_with_orders = {
            "count": users_with_orders_count,
            "total_users": total_users,
            "percentage": (
                round(users_with_orders_count * 100 / total_users, 1) if total_users else 0.0
            ),
        }

        orders_by_location = {
            "by_city": [
                {"city": row["address__city"] or "Sin especificar", "count": row["count"]}
                for row in base_qs.values("address__city").annotate(count=Count("id")).order_by("-count")
            ],
            "by_state": [
                {"state": row["address__state"] or "Sin especificar", "count": row["count"]}
                for row in base_qs.values("address__state").annotate(count=Count("id")).order_by("-count")
            ],
        }

        return Response({
            "orders_by_month": orders_by_month,
            "orders_by_status": orders_by_status,
            "cancellation_rate": cancellation_rate,
            "avg_time_between_statuses": avg_time_between_statuses,
            "top_users_by_orders": top_users_by_orders,
            "users_with_orders": users_with_orders,
            "orders_by_location": orders_by_location,
        })
