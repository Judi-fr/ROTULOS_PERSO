"""GET /api/v1/orders/stats/?days=7|30|90 — las estadísticas del comercio
(pestaña Estadísticas de Mi comercio, 2026-10-30).

Tres bloques, cada uno con su permiso y acceso de equipo, que el dueño reparte
en los perfiles: ``dispatch`` (cuánto tarda un pedido en salir),
``carriers`` (cómo rinde cada transportista) y ``buyers`` (dónde están los
compradores). La respuesta trae solo los bloques que la persona puede ver
(``allowed``); sin ninguno, 403.

Mismo alcance que la lista de pedidos: el comercio actual y, para un empleado
limitado, sus tiendas más los cargados a mano (``teams.order_scope_q``).
Un pedido "salió" en su primer evento despachado/en tránsito/entregado (uno
sincronizado de la tienda puede saltar directo a entregado), igual que la
semana del panel (``summary_views.SHIPPED``).
"""

from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
from statistics import median

from django.db.models import Min, Q
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions_map import user_has_permission
from apps.accounts.teams import data_owner, order_scope_q
from apps.carriers.models import CarrierShipment

from .models import Order
from .summary_views import PENDING, SHIPPED, SHIPMENT_PROBLEMS

PERIODS = (7, 30, 90)
DEFAULT_DAYS = 30
SECTIONS = (
    ("dispatch", "stats.dispatch"),
    ("carriers", "stats.carriers"),
    ("buyers", "stats.buyers"),
)
TOP_PLACES = 10
NO_CARRIER = "Sin indicar"


class BusinessStatsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        allowed = [name for name, permission in SECTIONS if user_has_permission(request.user, permission)]
        if not allowed:
            raise PermissionDenied("No tenés acceso a las estadísticas del comercio.")
        raw_days = request.query_params.get("days", str(DEFAULT_DAYS))
        if not raw_days.isdigit() or int(raw_days) not in PERIODS:
            raise ValidationError({"days": "Elegí 7, 30 o 90 días."})
        days = int(raw_days)
        start = timezone.now() - timedelta(days=days)

        orders = Order.objects.filter(order_scope_q(request.user), user=data_owner(request.user)).annotate(
            shipped_at=Min("status_events__created_at", filter=Q(status_events__status__in=SHIPPED)),
            delivered_at=Min("status_events__created_at", filter=Q(status_events__status=Order.Status.DELIVERED)),
        )
        payload = {"days": days, "allowed": allowed}
        if "dispatch" in allowed:
            payload["dispatch"] = dispatch_stats(orders.filter(created_at__gte=start))
        if "carriers" in allowed:
            payload["carriers"] = carrier_stats(orders.filter(shipped_at__gte=start))
        if "buyers" in allowed:
            payload["buyers"] = buyer_stats(orders.filter(created_at__gte=start))
        return Response(payload)


def percent(part, total):
    return round(100 * part / total) if total else None


def dispatch_stats(orders):
    """De los pedidos que entraron en el período: cuántas horas tardaron en
    salir (promedio y mediana), qué parte salió en 24 y 48 horas y cuántos
    siguen esperando."""
    hours = [
        max(0.0, (row["shipped_at"] - row["created_at"]).total_seconds() / 3600)
        for row in orders.exclude(shipped_at=None).values("created_at", "shipped_at")
    ]
    shipped = len(hours)
    return {
        "shipped": shipped,
        "waiting": orders.filter(shipped_at=None, status__in=PENDING).count(),
        "avg_hours": round(sum(hours) / shipped, 1) if shipped else None,
        "median_hours": round(median(hours), 1) if shipped else None,
        "within_24h": percent(sum(1 for value in hours if value <= 24), shipped),
        "within_48h": percent(sum(1 for value in hours if value <= 48), shipped),
    }


def carrier_stats(orders):
    """Por transportista (``Order.carrier``, el de API o el escrito a mano), de
    los pedidos que salieron en el período: cuántos, cuántos se entregaron y
    en cuántos días, qué parte tuvo un problema o volvió, y lo que costó según
    la cotización de sus envíos. Del que más despachó al que menos."""
    rows = list(orders.values("id", "carrier", "shipped_at", "delivered_at"))
    order_ids = [row["id"] for row in rows]
    shipments = CarrierShipment.objects.filter(order_id__in=order_ids).exclude(status=CarrierShipment.Status.CANCELLED)
    troubled = set(shipments.filter(status__in=SHIPMENT_PROBLEMS).values_list("order_id", flat=True))
    prices = defaultdict(list)
    for order_id, price in shipments.exclude(quoted_price=None).values_list("order_id", "quoted_price"):
        prices[order_id].append(price)

    groups = defaultdict(list)
    for row in rows:
        groups[(row["carrier"] or "").strip() or NO_CARRIER].append(row)
    result = []
    for name, group in groups.items():
        delivery_days = [
            (row["delivered_at"] - row["shipped_at"]).total_seconds() / 86400
            for row in group
            if row["delivered_at"] and row["delivered_at"] >= row["shipped_at"]
        ]
        costs = [price for row in group for price in prices.get(row["id"], [])]
        problems = sum(1 for row in group if row["id"] in troubled)
        total = sum(costs, Decimal("0"))
        result.append(
            {
                "carrier": name,
                "shipped": len(group),
                "delivered": sum(1 for row in group if row["delivered_at"]),
                "avg_delivery_days": round(sum(delivery_days) / len(delivery_days), 1) if delivery_days else None,
                "problems": problems,
                "problem_rate": percent(problems, len(group)),
                "cost_total": f"{total:.2f}" if costs else None,
                "cost_avg": f"{total / len(costs):.2f}" if costs else None,
            }
        )
    return sorted(result, key=lambda row: (-row["shipped"], row["carrier"].casefold()))


def buyer_stats(orders):
    """Dónde están los compradores de los pedidos del período (sin los
    cancelados): las provincias y ciudades con más pedidos. Las ciudades se
    juntan sin distinguir mayúsculas ni espacios ("CABA" y "caba ")."""
    rows = list(
        orders.exclude(status=Order.Status.CANCELLED).values_list("address__city", "address__state")
    )
    states = Counter()
    state_names = {}
    cities = Counter()
    city_names = {}
    for city, state in rows:
        city, state = (city or "").strip(), (state or "").strip()
        if state:
            states[state.casefold()] += 1
            state_names.setdefault(state.casefold(), state)
        if city:
            key = (city.casefold(), state.casefold())
            cities[key] += 1
            city_names.setdefault(key, (city, state))
    return {
        "total": len(rows),
        "states": [{"name": state_names[key], "count": count} for key, count in states.most_common(TOP_PLACES)],
        "cities": [
            {"name": city_names[key][0], "state": city_names[key][1], "count": count}
            for key, count in cities.most_common(TOP_PLACES)
        ],
    }
