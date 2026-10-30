"""GET /api/v1/orders/summary/ — lo que el panel muestra como "Hoy": cuántos
pedidos del comercio están para imprimir, para despachar, en camino y con
problemas en el transportista.

Mismo alcance que la lista de pedidos: los del comercio actual (su usuario de
datos) y, para un empleado limitado a algunas tiendas, solo las suyas más los
cargados a mano (``teams.order_scope_q``). Los avisos del comercio no se
cuentan acá: el panel ya los lee de ``/notices/``.
"""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.role_permissions import HasRolePermission
from apps.accounts.teams import data_owner, order_scope_q
from apps.carriers.models import CarrierShipment

from .models import Order

PENDING = (Order.Status.CREATED, Order.Status.PREPARING)
ON_THE_WAY = (Order.Status.DISPATCHED, Order.Status.IN_TRANSIT)
SHIPMENT_PROBLEMS = (CarrierShipment.Status.ISSUE, CarrierShipment.Status.RETURNING)


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
            }
        )
