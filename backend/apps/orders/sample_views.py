"""/api/v1/orders/sample/ — pedidos de ejemplo para un comercio que todavía no
conectó nada: con ellos imprime su primer rótulo en un minuto y ve cómo
funciona todo, y después los borra con un botón sin tocar sus pedidos reales.

GET ``{count}``; POST los crea (201; si ya hay, 200 y no duplica); DELETE los
borra (``{deleted, kept}``: uno al que ya se le creó un envío con un
transportista es un envío de verdad y queda, para cancelarlo a mano).
"""

from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.role_permissions import HasRolePermission
from apps.accounts.teams import data_owner
from apps.audit.services import record
from apps.carriers.models import CarrierShipment

from .models import Address, Order

# Compradores inventados, de ciudades distintas, para que los rótulos se vean
# como los de verdad. Nada de datos de contacto: el rótulo nunca los lleva.
SAMPLES = [
    {"recipient_name": "Lucía Fernández", "street": "Av. Corrientes", "number": "3247", "city": "CABA",
     "state": "Ciudad Autónoma de Buenos Aires", "postal_code": "1193", "description": "Ejemplo · 2x Remera"},
    {"recipient_name": "Martín Gómez", "street": "Bv. Oroño", "number": "850", "city": "Rosario",
     "state": "Santa Fe", "postal_code": "2000", "description": "Ejemplo · 1x Buzo"},
    {"recipient_name": "Sofía Castro", "street": "Av. Colón", "number": "1234", "city": "Córdoba",
     "state": "Córdoba", "postal_code": "5000", "description": "Ejemplo · 3x Medias"},
    {"recipient_name": "Joaquín Díaz", "street": "San Martín", "number": "1500", "city": "Mendoza",
     "state": "Mendoza", "postal_code": "5500", "description": "Ejemplo · 1x Gorra"},
]


class SampleOrdersView(APIView):
    def get_permissions(self):
        permission = "orders.view" if self.request.method == "GET" else "orders.create_manual"
        return [IsAuthenticated(), HasRolePermission(permission)]

    def samples(self, request):
        return Order.objects.filter(user=data_owner(request.user), is_sample=True)

    def get(self, request):
        return Response({"count": self.samples(request).count()})

    def post(self, request):
        owner = data_owner(request.user)
        existing = self.samples(request).count()
        if existing:
            return Response({"count": existing})
        with transaction.atomic():
            for sample in SAMPLES:
                fields = dict(sample)
                description = fields.pop("description")
                address = Address.objects.create(user=owner, origin=Address.Origin.SHIPMENT, **fields)
                order = Order(
                    user=owner, address=address, description=description,
                    source=Order.Source.MANUAL, is_sample=True,
                )
                order._skip_status_audit = True
                order.save()
        record(request, actor=request.user, category="orders", action="order.create",
               target=owner, changes={"sample_orders": {"from": 0, "to": len(SAMPLES)}})
        return Response({"count": len(SAMPLES)}, status=status.HTTP_201_CREATED)

    def delete(self, request):
        samples = self.samples(request)
        # Un envío cancelado ya no existe para el transportista: no retiene el
        # pedido (por eso el aviso puede decir "cancelá ese envío").
        live = CarrierShipment.objects.exclude(status=CarrierShipment.Status.CANCELLED)
        kept = samples.filter(carrier_shipments__in=live).distinct()
        removable = samples.exclude(pk__in=kept.values("pk"))
        addresses = list(removable.values_list("address_id", flat=True))
        deleted = removable.count()
        with transaction.atomic():
            CarrierShipment.objects.filter(order__in=removable).delete()
            removable.delete()
            Address.objects.filter(pk__in=addresses, origin=Address.Origin.SHIPMENT, orders__isnull=True).delete()
        return Response({"deleted": deleted, "kept": kept.count()})

