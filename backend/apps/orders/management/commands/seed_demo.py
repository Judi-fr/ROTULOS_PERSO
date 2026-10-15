"""``python manage.py seed_demo`` — datos de ejemplo para desarrollo y demos.

Crea (o completa, si ya existen) una cuenta de comerciante con:

- una tienda conectada de ejemplo (Empretienda: es la única plataforma que no
  habla con ninguna API, así que la tienda no dispara llamadas reales ni
  eventos del worker), con remitente y una tabla de tarifas de envío;
- pedidos de esa tienda en todos los estados, entrados por el mismo camino
  que los reales (``ingestion.upsert_store_order``) y repartidos en los
  últimos días para que los filtros por fecha muestren algo;
- algunos pedidos cargados a mano, sin tienda;
- una dirección propia.

**Idempotente**: correrlo de nuevo no duplica nada (la tienda se busca por
plataforma + dominio, los pedidos por su ``external_id``). Se niega a correr
con ``DEBUG`` apagado, salvo ``--force``: no es algo para producción.

Los nombres, calles y teléfonos son inventados.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.integrations.models import ShippingRate, StoreConnection
from apps.integrations.providers import NormalizedOrder
from apps.orders.ingestion import upsert_store_order
from apps.orders.models import Address, Order

DEMO_EMAIL = "demo@rotulos.local"
# Cumple la regla de contraseñas (6+, una mayúscula, un número). Es una cuenta
# de desarrollo; se cambia con --password.
DEMO_PASSWORD = "Demo2026"
DEMO_STORE_DOMAIN = "tienda-demo.empretienda.com.ar"

# (número, destinatario, calle, altura, ciudad, provincia, CP, estado, peso kg, días atrás, transportista, seguimiento)
STORE_ORDERS = [
    ("1001", "Lucía Fernández", "Av. Corrientes", "1234", "Ciudad Autónoma de Buenos Aires", "Ciudad Autónoma de Buenos Aires", "C1043AAZ", "created", "0.8", 0, "", ""),
    ("1002", "Martín Gómez", "San Martín", "455", "Rosario", "Santa Fe", "S2000", "created", "1.5", 0, "", ""),
    ("1003", "Sofía Rodríguez", "Bv. Chacabuco", "890", "Córdoba", "Córdoba", "X5000", "created", "2.2", 1, "", ""),
    ("1004", "Julián Pérez", "Av. Colón", "2100", "Mar del Plata", "Buenos Aires", "B7600", "preparing", "0.5", 1, "", ""),
    ("1005", "Valentina López", "Belgrano", "77", "Mendoza", "Mendoza", "M5500", "preparing", "3.0", 2, "", ""),
    ("1006", "Tomás Díaz", "Mitre", "1500", "San Miguel de Tucumán", "Tucumán", "T4000", "dispatched", "1.1", 3, "Andreani", "360000012345670"),
    ("1007", "Camila Martínez", "Av. Libertador", "3200", "Vicente López", "Buenos Aires", "B1638", "dispatched", "0.9", 3, "OCA", "3867500000001234567"),
    ("1008", "Nicolás Romero", "25 de Mayo", "610", "Neuquén", "Neuquén", "Q8300", "in_transit", "4.5", 5, "Correo Argentino", "CP123456789AR"),
    ("1009", "Agustina Sosa", "Rivadavia", "980", "Salta", "Salta", "A4400", "delivered", "0.6", 8, "Andreani", "360000012345671"),
    ("1010", "Federico Álvarez", "Sarmiento", "345", "La Plata", "Buenos Aires", "B1900", "cancelled", "1.0", 6, "", ""),
]

MANUAL_ORDERS = [
    ("demo-manual-1", "Carolina Ruiz", "Moreno", "250", "Bahía Blanca", "Buenos Aires", "B8000", "Pedido por WhatsApp"),
    ("demo-manual-2", "Diego Torres", "Av. San Juan", "1800", "Ciudad Autónoma de Buenos Aires", "Ciudad Autónoma de Buenos Aires", "C1147", "Venta en el local"),
]

# (desde CP, hasta CP, hasta kg, precio, días mín, días máx)
RATES = [
    ("1000", "1499", Decimal("5"), Decimal("4500"), 1, 2),
    ("1000", "1499", None, Decimal("7800"), 1, 3),
    ("1500", "9499", Decimal("5"), Decimal("8900"), 3, 6),
    ("1500", "9499", None, Decimal("13500"), 4, 8),
]


class Command(BaseCommand):
    help = "Crea una cuenta de comerciante con tienda, tarifas y pedidos de ejemplo (solo desarrollo)."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=DEMO_EMAIL, help=f"Cuenta a crear o completar (por defecto {DEMO_EMAIL}).")
        parser.add_argument("--password", default=DEMO_PASSWORD, help="Contraseña si la cuenta se crea ahora.")
        parser.add_argument("--force", action="store_true", help="Correr aunque DEBUG esté apagado.")

    def handle(self, *args, email, password, force, **options):
        if not settings.DEBUG and not force:
            raise CommandError("DEBUG está apagado: esto parece producción. Usá --force si de verdad querés datos de ejemplo acá.")

        with transaction.atomic():
            user, user_created = self._user(email.strip().lower(), password)
            store = self._store(user)
            rates = self._rates(store)
            created_store = self._store_orders(store)
            created_manual = self._manual_orders(user)
            self._own_address(user)

        self.stdout.write(self.style.SUCCESS("Datos de ejemplo listos."))
        self.stdout.write(f"  Cuenta: {user.email} ({'creada' if user_created else 'ya existía, no se cambió la contraseña'})")
        self.stdout.write(f"  Tienda: {store.name} (id {store.pk})")
        self.stdout.write(f"  Tarifas nuevas: {rates}")
        self.stdout.write(f"  Pedidos nuevos: {created_store} de la tienda, {created_manual} cargados a mano")

    # -- piezas -------------------------------------------------------------

    def _user(self, email, password):
        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=email,
            defaults={"email": email, "first_name": "Comercio", "last_name": "Demo"},
        )
        if created:
            user.set_password(password)
            user.save()
        # operator: el self-service más la carga manual e importación, que es
        # lo que hace un comerciante de verdad.
        group, _ = Group.objects.get_or_create(name="operator")
        user.groups.add(group)
        return user, created

    def _store(self, user):
        store, created = StoreConnection.objects.get_or_create(
            platform=StoreConnection.Platform.EMPRETIENDA,
            external_store_id=DEMO_STORE_DOMAIN,
            defaults={
                "owner": user,
                "name": "Tienda Demo",
                "store_url": f"https://{DEMO_STORE_DOMAIN}",
                "sender_name": "Tienda Demo",
                "sender_address": "Av. Santa Fe 1000, CABA",
                "sender_phone": "11 4000-0000",
            },
        )
        if not created and store.owner_id != user.pk:
            raise CommandError(f"La tienda de ejemplo ya existe y es de otra cuenta (id {store.owner_id}).")
        return store

    def _rates(self, store):
        created = 0
        for code_from, code_to, up_to, price, days_min, days_max in RATES:
            _, was_created = ShippingRate.objects.get_or_create(
                connection=store,
                option_code="standard",
                postal_code_from=code_from,
                postal_code_to=code_to,
                weight_up_to_kg=up_to,
                defaults={"price": price, "delivery_days_min": days_min, "delivery_days_max": days_max},
            )
            created += was_created
        return created

    def _store_orders(self, store):
        now = timezone.now()
        created = 0
        for (number, name, street, height, city, state, postal_code, status, weight, days_ago, carrier, tracking) in STORE_ORDERS:
            order, result = upsert_store_order(
                store,
                NormalizedOrder(
                    external_id=f"demo-{number}",
                    external_number=number,
                    recipient_name=name,
                    street=street,
                    number=height,
                    city=city,
                    state=state,
                    postal_code=postal_code,
                    shipping_option="Envío estándar",
                    status=status,
                    total_weight_kg=Decimal(weight),
                    items=[{"name": "Producto de ejemplo", "quantity": 1}],
                ),
            )
            if result != "created":
                continue
            created += 1
            # Fechas, seguimiento y "preparing" sin pasar por save(): no es un
            # cambio de estado real, no tiene que dejar eventos ni auditoría.
            # "preparing" se pone acá porque una tienda nunca lo informa (es
            # el paso local antes de despachar, ver Order.SHIPPING_STATUSES).
            Order.objects.filter(pk=order.pk).update(
                status=status,
                created_at=now - timedelta(days=days_ago, hours=created),
                carrier=carrier,
                tracking_number=tracking,
            )
        return created

    def _manual_orders(self, user):
        created = 0
        for external_id, name, street, height, city, state, postal_code, description in MANUAL_ORDERS:
            if Order.objects.filter(user=user, store_connection=None, external_id=external_id).exists():
                continue
            address = Address.objects.create(
                user=user,
                origin=Address.Origin.SHIPMENT,
                recipient_name=name,
                street=street,
                number=height,
                city=city,
                state=state,
                postal_code=postal_code,
            )
            Order.objects.create(
                user=user,
                address=address,
                external_id=external_id,
                source=Order.Source.MANUAL,
                description=description,
            )
            created += 1
        return created

    def _own_address(self, user):
        if Address.objects.filter(user=user, origin=Address.Origin.OWN).exists():
            return
        Address.objects.create(
            user=user,
            label="Depósito",
            recipient_name="Tienda Demo",
            street="Av. Santa Fe",
            number="1000",
            city="Ciudad Autónoma de Buenos Aires",
            state="Ciudad Autónoma de Buenos Aires",
            postal_code="C1059",
            is_default=True,
        )
