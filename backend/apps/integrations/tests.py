"""Tests de integraciones (stories 23-24).

Sigue el mismo patrón que apps/orders/tests.py: pega contra los endpoints
reales.
"""

import hashlib
import hmac
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from apps.orders.models import Address, Order

from .models import IncomingWebhook, IntegrationKey, WebhookDelivery, WebhookEndpoint

User = get_user_model()


def auth_headers_for(user):
    token = RefreshToken.for_user(user)
    return {"HTTP_AUTHORIZATION": f"Bearer {token.access_token}"}


class IncomingWebhookSignatureTests(APITestCase):
    """Un webhook entrante con firma incorrecta: lo que se rompe en
    silencio si sale mal es aceptar un payload sin verificar quién lo
    mandó, y de paso crear un pedido con datos de cualquiera."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="tienda@example.com",
            email="tienda@example.com",
            password="Clave123!",
        )
        self.integration_key = IntegrationKey.objects.create(
            owner=self.owner,
            name="Tienda X",
            key_hash=IntegrationKey.hash_key("bp_test-key"),
            prefix="bp_test-ke",
        )
        self.secret = "un-secreto-compartido"
        self.webhook = IncomingWebhook.objects.create(
            owner=self.owner,
            integration_key=self.integration_key,
            name="Tienda X - pedidos",
            slug="tienda-x",
            secret=self.secret,
            mapping={
                "destinatario": "cliente",
                "domicilio": "calle",
                "ciudad": "ciudad",
                "external_id": "id",
            },
        )
        self.payload = {
            "cliente": "Juan Pérez",
            "calle": "Av. Siempre Viva 742",
            "ciudad": "CABA",
            "id": "TIENDA-0001",
        }
        self.body = json.dumps(self.payload).encode("utf-8")

    def _post(self, body, signature):
        headers = {}
        if signature is not None:
            headers["HTTP_X_WEBHOOK_SIGNATURE"] = signature
        return self.client.post(
            "/api/v1/ingest/webhooks/tienda-x/",
            data=body,
            content_type="application/json",
            **headers,
        )

    def test_firma_incorrecta_devuelve_401_y_no_crea_pedido(self):
        response = self._post(self.body, signature="firma-invalida-a-mano")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Order.objects.count(), 0)

    def test_sin_firma_devuelve_401_y_no_crea_pedido(self):
        response = self._post(self.body, signature=None)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Order.objects.count(), 0)

    def test_firma_correcta_crea_el_pedido(self):
        valid_signature = hmac.new(self.secret.encode(), self.body, hashlib.sha256).hexdigest()
        response = self._post(self.body, signature=valid_signature)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Order.objects.count(), 1)
        order = Order.objects.get()
        self.assertEqual(order.user_id, self.owner.id)
        self.assertEqual(order.external_id, "TIENDA-0001")
        self.assertEqual(order.source, Order.Source.WEBHOOK)


class OutgoingWebhookDispatchTests(APITestCase):
    """``dispatch_event`` (story 24) tenía el enchufe suelto: nadie lo
    llamaba desde ``apps.orders``. Un pedido nuevo con un
    ``WebhookEndpoint`` activo tiene que generar una entrega; un lote
    (importación) NO tiene que generar una por fila — tiene que mandar un
    único evento resumen (``orders.imported``)."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="cliente-webhook@example.com",
            email="cliente-webhook@example.com",
            password="Clave123!",
        )
        self.endpoint = WebhookEndpoint.objects.create(
            owner=self.owner,
            name="ERP del cliente",
            url="https://erp.example.com/webhooks/buspack/",
            secret=WebhookEndpoint.generate_secret(),
        )

    @staticmethod
    def _ok_response(mock_post):
        mock_post.return_value.status_code = 200
        mock_post.return_value.ok = True
        mock_post.return_value.text = "ok"

    @patch("apps.integrations.webhooks.requests.post")
    def test_pedido_nuevo_dispara_un_webhook_saliente(self, mock_post):
        self._ok_response(mock_post)

        address = Address.objects.create(
            user=self.owner, street="Av. Siempre Viva", number="742", city="CABA"
        )
        Order.objects.create(user=self.owner, address=address, description="Paquete")

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(WebhookDelivery.objects.count(), 1)
        delivery = WebhookDelivery.objects.get()
        self.assertEqual(delivery.event, "order.created")
        self.assertTrue(delivery.success)

    @patch("apps.integrations.webhooks.requests.post")
    def test_importacion_no_dispara_un_webhook_por_fila(self, mock_post):
        self._ok_response(mock_post)

        operator_group, _ = Group.objects.get_or_create(name="operator")
        self.owner.groups.add(operator_group)

        headers = ["Destinatario", "Domicilio", "Ciudad", "ID Externo"]
        rows = [
            "Juan Pérez;Av. Siempre Viva;CABA;PED-0001",
            "María Gómez;Av. Rivadavia;CABA;PED-0002",
            "Carlos Ruiz;Av. Corrientes;CABA;PED-0003",
        ]
        csv_text = ";".join(headers) + "\n" + "\n".join(rows)
        file_obj = SimpleUploadedFile("pedidos.csv", csv_text.encode("utf-8"), content_type="text/csv")

        upload_response = self.client.post(
            "/api/v1/orders/imports/", {"file": file_obj}, **auth_headers_for(self.owner)
        )
        self.assertEqual(upload_response.status_code, 201, upload_response.data)

        mapping = {
            "destinatario": "Destinatario",
            "domicilio": "Domicilio",
            "ciudad": "Ciudad",
            "external_id": "ID Externo",
        }
        confirm_response = self.client.post(
            f"/api/v1/orders/imports/{upload_response.data['id']}/confirm/",
            {"mapping": mapping},
            format="json",
            **auth_headers_for(self.owner),
        )
        self.assertEqual(confirm_response.status_code, 200, confirm_response.data)
        self.assertEqual(confirm_response.data["imported_count"], 3)

        # Nada de 3 entregas (una por fila importada): un único evento resumen.
        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(WebhookDelivery.objects.count(), 1)
        delivery = WebhookDelivery.objects.get()
        self.assertEqual(delivery.event, "orders.imported")
        self.assertEqual(delivery.payload["count"], 3)
        self.assertEqual(delivery.payload["source"], "import")
