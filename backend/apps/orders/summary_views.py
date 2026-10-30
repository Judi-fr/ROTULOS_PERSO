"""GET /api/v1/orders/summary/ — lo que el panel muestra como "Hoy": cuántos
pedidos del comercio están para imprimir, para despachar, en camino y con
problemas en el transportista, y cuántos pendientes esperan hace más de
``DELAYED_AFTER_DAYS`` días (los "demorados", que más reclamos generan).
También el desglose por tienda de "para imprimir"/"para despachar"
(``by_store``) y la semana: por día, cuántos pedidos entraron y cuántos
salieron (``week``), para ver si el trabajo se acumula.

Mismo alcance que la lista de pedidos: los del comercio actual (su usuario de
datos) y, para un empleado limitado a algunas tiendas, solo las suyas más los
cargados a mano (``teams.order_scope_q``). Los avisos del comercio no se
cuentan acá: el panel ya los lee de ``/notices/``.
"""

from datetime import datetime, time, timedelta

from django.db.models import Count, Min, Q
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.role_permissions import HasRolePermission
from apps.accounts.teams import data_owner, order_scope_q
from apps.carriers.models import CarrierShipment

from .models import Order
from .views import created_before

PENDING = (Order.Status.CREATED, Order.Status.PREPARING)
ON_THE_WAY = (Order.Status.DISPATCHED, Order.Status.IN_TRANSIT)
SHIPMENT_PROBLEMS = (CarrierShipment.Status.ISSUE, CarrierShipment.Status.RETURNING)
DELAYED_AFTER_DAYS = 2
# Un pedido "salió" el día de su primer evento en cualquiera de estos estados:
# uno sincronizado de la tienda puede saltar directo a en tránsito/entregado.
SHIPPED = (Order.Status.DISPATCHED, Order.Status.IN_TRANSIT, Order.Status.DELIVERED)
WEEK_DAYS = 7
MANUAL_STORE_NAME = "Cargados a mano"


class OrderSummaryView(APIView):
    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("orders.view")]

    def get(self, request):
        orders = Order.objects.filter(order_scope_q(request.user), user=data_owner(request.user))
        pending = orders.filter(status__in=PENDING)
        problems = (
            CarrierShipment.objects.filter(order__in=orders, status__in=SHIPMENT_PROBLEMS)
            .values("order_id")
            .distinct()
            .count()
        )
        return Response(
            {
                "to_print": pending.filter(labels_printed_at=None).count(),
                "to_dispatch": pending.exclude(labels_printed_at=None).count(),
                "on_the_way": orders.filter(status__in=ON_THE_WAY).count(),
                "shipment_problems": problems,
                "delayed": pending.filter(created_at__lt=created_before(DELAYED_AFTER_DAYS)).count(),
                "delayed_days": DELAYED_AFTER_DAYS,
                "by_store": by_store(pending),
                "week": week(orders),
            }
        )


def by_store(pending):
    """Para imprimir / para despachar por tienda, en el orden de alta de las
    tiendas (el mismo de la columna de Mis pedidos) y los cargados a mano al
    final. Una tienda sin nada pendiente no aparece."""
    rows = (
        pending.values("store_connection", "store_connection__name")
        .annotate(
            to_print=Count("id", filter=Q(labels_printed_at=None)),
            to_dispatch=Count("id", filter=Q(labels_printed_at__isnull=False)),
        )
        .order_by("store_connection")
    )
    result = [
        {
            "store": row["store_connection"] or "manual",
            "name": (row["store_connection__name"] or "Tienda") if row["store_connection"] else MANUAL_STORE_NAME,
            "to_print": row["to_print"],
            "to_dispatch": row["to_dispatch"],
        }
        for row in rows
    ]
    return sorted(result, key=lambda row: row["store"] == "manual")


def week(orders):
    """Los últimos ``WEEK_DAYS`` días (hoy incluido, del más viejo al más
    nuevo): cuántos pedidos entraron y cuántos salieron cada día, en hora local."""
    today = timezone.localdate()
    days = [today - timedelta(days=offset) for offset in range(WEEK_DAYS - 1, -1, -1)]
    start = timezone.make_aware(datetime.combine(days[0], time.min))
    entered = {day: 0 for day in days}
    dispatched = {day: 0 for day in days}
    for created_at in orders.filter(created_at__gte=start).values_list("created_at", flat=True):
        entered[timezone.localdate(created_at)] += 1
    first_shipped = (
        orders.annotate(shipped_at=Min("status_events__created_at", filter=Q(status_events__status__in=SHIPPED)))
        .filter(shipped_at__gte=start)
        .values_list("shipped_at", flat=True)
    )
    for shipped_at in first_shipped:
        dispatched[timezone.localdate(shipped_at)] += 1
    return [{"date": day.isoformat(), "entered": entered[day], "dispatched": dispatched[day]} for day in days]
