"""Tests de la base multi-tienda: conexiones con token cifrado,
idempotencia de pedidos por tienda, proveedor Tiendanube (firma y
normalización) y cola de eventos con reintentos."""

import base64
import hashlib
import hmac
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.orders.ingestion import create_order_from_data, upsert_store_order
from apps.orders.models import Order

from .. import events
from ..events import PermanentEventError, enqueue_event, process_due_events
from ..models import IntegrationEvent, StoreConnection
from ..providers import get_provider

User = get_user_model()


def tiendanube_order(**overrides):
    """Pedido con la forma de la API de Tiendanube (resource Order)."""
    order = {
        "id": 871254203,
        "number": 1001,
        "contact_name": "Juan Pérez",
        "contact_email": "juan@example.com",
        "contact_phone": "+54 11 5555-0000",
        "shipping_option": "Envío a domicilio",
        "created_at": "2026-09-10T12:00:00+0000",
        "updated_at": "2026-09-10T12:05:00+0000",
        "shipping_address": {
            "name": "Juan Pérez",
            "address": "Av. Siempre Viva",
            "number": "742",
            "floor": "3B",
            "locality": "Palermo",
            "city": "CABA",
            "province": "Capital Federal",
            "zipcode": "1425",
            "country": "AR",
            "phone": "+54 11 5555-0000",
        },
        "products": [
            {"name": "Remera", "sku": "REM-01", "quantity": 2, "weight": "0.250"},
            {"name": "Gorra", "sku": "", "quantity": 1, "weight": "0.100"},
        ],
    }
    order.update(overrides)
    return order


def make_connection(owner, store_id="1234567", name="Tienda A"):
    return StoreConnection.objects.create(
        owner=owner,
        platform=StoreConnection.Platform.TIENDANUBE,
        external_store_id=store_id,
        name=name,
    )


class StoreConnectionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="comercio@example.com", email="comercio@example.com")

    def test_el_token_se_guarda_cifrado_y_se_lee_en_claro(self):
        connection = make_connection(self.owner)
        connection.access_token = "token-secreto-de-la-tienda"
        connection.save()

        stored = StoreConnection.objects.get(pk=connection.pk)
        self.assertNotIn("token-secreto-de-la-tienda", stored.access_token_encrypted)
        self.assertNotEqual(stored.access_token_encrypted, "")
        self.assertEqual(stored.access_token, "token-secreto-de-la-tienda")

    def test_token_ilegible_con_otra_clave_falla_explicito(self):
        connection = make_connection(self.owner)
        connection.access_token = "token-secreto"
        connection.save()

        with override_settings(INTEGRATIONS_ENCRYPTION_KEY=base64.urlsafe_b64encode(b"x" * 32).decode()):
            with self.assertRaises(ValueError):
                StoreConnection.objects.get(pk=connection.pk).access_token

    def test_la_misma_tienda_no_se_puede_conectar_dos_veces(self):
        make_connection(self.owner, store_id="999")
        other = User.objects.create_user(username="otro@example.com", email="otro@example.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_connection(other, store_id="999")


class StoreOrderUpsertTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="comercio@example.com", email="comercio@example.com")
        self.store_a = make_connection(self.owner, store_id="111", name="Tienda A")
        self.store_b = make_connection(self.owner, store_id="222", name="Tienda B")
        self.provider = get_provider(StoreConnection.Platform.TIENDANUBE)

    def test_crea_el_pedido_con_los_datos_de_envio(self):
        order, result = upsert_store_order(self.store_a, self.provider.normalize_order(tiendanube_order()))

        self.assertEqual(result, "created")
        self.assertEqual(order.user, self.owner)
        self.assertEqual(order.store_connection, self.store_a)
        self.assertEqual(order.source, Order.Source.STORE)
        self.assertEqual(order.external_id, "871254203")
        self.assertEqual(order.external_number, "1001")
        self.assertEqual(order.contact_email, "juan@example.com")
        self.assertEqual(order.total_weight_kg, Decimal("0.600"))
        self.assertEqual(len(order.items), 2)
        self.assertEqual(order.address.recipient_name, "Juan Pérez")
        self.assertEqual(order.address.city, "CABA")
        self.assertEqual(order.address.country, "Argentina")
        self.assertIn("3B", order.address.reference)

    def test_el_mismo_aviso_repetido_no_duplica(self):
        normalized = self.provider.normalize_order(tiendanube_order())
        upsert_store_order(self.store_a, normalized)
        _, result = upsert_store_order(self.store_a, normalized)

        self.assertEqual(result, "unchanged")
        self.assertEqual(Order.objects.count(), 1)

    def test_mismo_pedido_en_dos_tiendas_del_mismo_cliente_son_dos_pedidos(self):
        normalized = self.provider.normalize_order(tiendanube_order())
        upsert_store_order(self.store_a, normalized)
        upsert_store_order(self.store_b, normalized)

        self.assertEqual(Order.objects.filter(user=self.owner, external_id="871254203").count(), 2)

    def test_version_mas_nueva_actualiza_y_una_vieja_llegada_tarde_se_ignora(self):
        upsert_store_order(self.store_a, self.provider.normalize_order(tiendanube_order()))

        newer = tiendanube_order(updated_at="2026-09-10T13:00:00+0000")
        newer["shipping_address"] = {**newer["shipping_address"], "city": "Rosario"}
        order, result = upsert_store_order(self.store_a, self.provider.normalize_order(newer))
        self.assertEqual(result, "updated")
        self.assertEqual(order.address.city, "Rosario")

        older = tiendanube_order(updated_at="2026-09-10T12:30:00+0000")
        older["shipping_address"] = {**older["shipping_address"], "city": "Córdoba"}
        order, result = upsert_store_order(self.store_a, self.provider.normalize_order(older))
        self.assertEqual(result, "unchanged")
        order.address.refresh_from_db()
        self.assertEqual(order.address.city, "Rosario")
        self.assertEqual(Order.objects.count(), 1)

    def test_pedido_de_tienda_no_bloquea_una_carga_manual_con_el_mismo_external_id(self):
        upsert_store_order(self.store_a, self.provider.normalize_order(tiendanube_order()))

        _, created = create_order_from_data(
            self.owner,
            {"destinatario": "Otra persona", "domicilio": "Calle 1", "ciudad": "La Plata", "external_id": "871254203"},
            source=Order.Source.MANUAL,
        )

        self.assertTrue(created)
        self.assertEqual(Order.objects.count(), 2)

    def test_la_carga_manual_sigue_siendo_idempotente_por_usuario(self):
        data = {"destinatario": "Ana", "domicilio": "Calle 1", "ciudad": "La Plata", "external_id": "PED-1"}
        create_order_from_data(self.owner, data, source=Order.Source.MANUAL)
        _, created = create_order_from_data(self.owner, data, source=Order.Source.MANUAL)

        self.assertFalse(created)
        self.assertEqual(Order.objects.count(), 1)

    def test_datos_mas_largos_que_el_campo_se_recortan(self):
        raw = tiendanube_order()
        raw["shipping_address"] = {**raw["shipping_address"], "number": "1" * 60}
        order, _ = upsert_store_order(self.store_a, self.provider.normalize_order(raw))

        self.assertEqual(len(order.address.number), 20)


@override_settings(TIENDANUBE_CLIENT_SECRET="secreto-de-la-app")
class TiendanubeProviderTests(TestCase):
    def setUp(self):
        self.provider = get_provider("tiendanube")
        self.body = b'{"store_id": 111, "event": "order/paid", "id": 871254203}'
        self.digest = hmac.new(b"secreto-de-la-app", self.body, hashlib.sha256).digest()

    def test_firma_valida_en_hex(self):
        headers = {"x-linkedstore-hmac-sha256": self.digest.hex()}
        self.assertTrue(self.provider.verify_webhook(self.body, headers))

    def test_firma_valida_en_base64(self):
        headers = {"X-Linkedstore-Hmac-Sha256": base64.b64encode(self.digest).decode()}
        self.assertTrue(self.provider.verify_webhook(self.body, headers))

    def test_firma_de_otro_body_se_rechaza(self):
        headers = {"x-linkedstore-hmac-sha256": self.digest.hex()}
        self.assertFalse(self.provider.verify_webhook(self.body + b" ", headers))

    def test_sin_firma_se_rechaza(self):
        self.assertFalse(self.provider.verify_webhook(self.body, {}))

    def test_firma_con_caracteres_raros_se_rechaza_sin_romper(self):
        self.assertFalse(self.provider.verify_webhook(self.body, {"x-linkedstore-hmac-sha256": "ñandú"}))

    @override_settings(TIENDANUBE_CLIENT_SECRET="")
    def test_sin_secret_configurado_se_rechaza_todo(self):
        headers = {"x-linkedstore-hmac-sha256": self.digest.hex()}
        self.assertFalse(self.provider.verify_webhook(self.body, headers))

    def test_pedido_sin_id_no_se_puede_normalizar(self):
        with self.assertRaises(ValueError):
            self.provider.normalize_order(tiendanube_order(id=None))

    def test_shipping_option_como_objeto_y_sin_pesos_de_producto(self):
        raw = tiendanube_order(shipping_option={"name": "Retiro en sucursal"}, weight="1.5")
        raw["products"] = [{"name": "Caja", "quantity": 1}]
        normalized = self.provider.normalize_order(raw)

        self.assertEqual(normalized.shipping_option, "Retiro en sucursal")
        self.assertEqual(normalized.total_weight_kg, Decimal("1.5"))
        self.assertEqual(normalized.description, "1x Caja")

    def test_plataforma_desconocida(self):
        with self.assertRaises(ValueError):
            get_provider("plataforma-inventada")


@override_settings(INTEGRATIONS_EVENT_RETRY_BASE_SECONDS=60, INTEGRATIONS_EVENT_MAX_ATTEMPTS=3)
class IntegrationEventQueueTests(TestCase):
    PLATFORM = "tiendanube"

    def setUp(self):
        self.calls = []

    def _enqueue(self, event_type, resource_id="1", payload=None):
        return enqueue_event(
            platform=self.PLATFORM,
            event_type=event_type,
            resource_id=resource_id,
            payload=payload or {"id": resource_id},
        )

    def _handlers(self, mapping):
        return patch.dict(events._HANDLERS, {(self.PLATFORM, key): value for key, value in mapping.items()})

    def test_aviso_repetido_mientras_esta_pendiente_no_se_encola_dos_veces(self):
        first, created_first = self._enqueue("test/ok")
        second, created_second = self._enqueue("test/ok")

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(IntegrationEvent.objects.count(), 1)

    def test_aviso_igual_a_uno_ya_procesado_se_vuelve_a_encolar(self):
        with self._handlers({"test/ok": self.calls.append}):
            self._enqueue("test/ok")
            process_due_events()
            _, created = self._enqueue("test/ok")

        self.assertTrue(created)

    def test_evento_procesado_ok_queda_done(self):
        event, _ = self._enqueue("test/ok")
        with self._handlers({"test/ok": self.calls.append}):
            counts = process_due_events()

        event.refresh_from_db()
        self.assertEqual(counts["done"], 1)
        self.assertEqual(event.status, IntegrationEvent.Status.DONE)
        self.assertEqual(event.attempts, 1)
        self.assertIsNotNone(event.processed_at)
        self.assertEqual(len(self.calls), 1)

    def test_fallo_temporal_se_reintenta_mas_tarde(self):
        def falla(event):
            raise RuntimeError("API caída")

        event, _ = self._enqueue("test/falla")
        with self._handlers({"test/falla": falla}):
            process_due_events()
            event.refresh_from_db()
            self.assertEqual(event.status, IntegrationEvent.Status.PENDING)
            self.assertEqual(event.attempts, 1)
            self.assertIn("API caída", event.last_error)
            self.assertGreater(event.next_attempt_at, timezone.now() + timedelta(seconds=50))

            # Todavía no venció: otra pasada del worker no lo toca.
            counts = process_due_events()
            self.assertEqual(sum(counts.values()), 0)

    @override_settings(INTEGRATIONS_EVENT_RETRY_BASE_SECONDS=0)
    def test_se_agotan_los_intentos_y_queda_failed(self):
        def falla(event):
            raise RuntimeError("sigue caída")

        event, _ = self._enqueue("test/falla")
        with self._handlers({"test/falla": falla}):
            for _ in range(3):
                process_due_events()

        event.refresh_from_db()
        self.assertEqual(event.status, IntegrationEvent.Status.FAILED)
        self.assertEqual(event.attempts, 3)
        self.assertIn("se agotaron", event.last_error)

    def test_error_permanente_no_se_reintenta(self):
        def desconectada(event):
            raise PermanentEventError("La tienda está desconectada.")

        event, _ = self._enqueue("test/permanente")
        with self._handlers({"test/permanente": desconectada}):
            process_due_events()

        event.refresh_from_db()
        self.assertEqual(event.status, IntegrationEvent.Status.FAILED)
        self.assertEqual(event.attempts, 1)

    def test_evento_sin_handler_queda_failed(self):
        event, _ = self._enqueue("test/sin-handler")
        process_due_events()

        event.refresh_from_db()
        self.assertEqual(event.status, IntegrationEvent.Status.FAILED)
        self.assertIn("No hay un handler", event.last_error)

    def test_evento_trabado_en_processing_se_vuelve_a_encolar(self):
        event, _ = self._enqueue("test/ok")
        IntegrationEvent.objects.filter(pk=event.pk).update(
            status=IntegrationEvent.Status.PROCESSING,
            updated_at=timezone.now() - timedelta(hours=1),
        )
        with self._handlers({"test/ok": self.calls.append}):
            process_due_events()

        event.refresh_from_db()
        self.assertEqual(event.status, IntegrationEvent.Status.DONE)

    def test_comando_worker_procesa_un_lote_y_termina(self):
        self._enqueue("test/ok")
        out = StringIO()
        with self._handlers({"test/ok": self.calls.append}):
            call_command("run_integrations_worker", "--once", stdout=out)

        self.assertEqual(IntegrationEvent.objects.get().status, IntegrationEvent.Status.DONE)
        self.assertIn("Eventos procesados: 1", out.getvalue())
