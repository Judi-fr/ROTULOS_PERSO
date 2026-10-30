"""El recorrido completo de un comercio nuevo, de punta a punta y solo por la API,
como lo hace el frontend: registrarse, crear el comercio, cargar sus datos,
sumar una tienda, traer los pedidos, imprimir los rótulos, despachar con un
transportista, imprimir rótulo + etiqueta, armar la planilla de retiro, seguir
el envío y exportar.

Cada pieza tiene sus propios tests; este existe para que, antes de una prueba
con clientes o de un deploy, una rotura en las COSTURAS entre apps (permisos de
equipo, el usuario de datos del comercio, un cambio de forma en una respuesta)
aparezca acá y no en la pantalla de un comerciante. Andreani es el simulado de
siempre; Empretienda no llama a nada.
"""

import io
from unittest.mock import patch

from django.core import mail
from django.test import override_settings
from pypdf import PdfReader
from rest_framework.test import APITestCase

from apps.carriers.andreani.tests.fake_andreani import PASSWORD, USER, AndreaniTestMixin, event
from apps.carriers.andreani.tests.test_printing import andreani_label
from apps.integrations.providers.empretienda.tests.test_empretienda import csv_file, row
from apps.orders.models import Order

API = "/api/v1"
EMAIL = "nuevo.comercio@example.com"
CLAVE = "Rotulos2026"


def pdf_text(response):
    content = b"".join(response.streaming_content) if response.streaming else response.content
    reader = PdfReader(io.BytesIO(content))
    return len(reader.pages), "\n".join(page.extract_text() or "" for page in reader.pages)


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CommercialFlowTests(AndreaniTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        # La etiqueta del simulado no es un PDF que se pueda abrir; la de test_printing sí.
        patcher = patch(
            "apps.carriers.andreani.client.AndreaniClient.label", autospec=True,
            side_effect=lambda client, number, **kw: andreani_label(number),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, method, path, data=None, expected=200, **extra):
        kwargs = {"format": "json"} if data is not None and "format" not in extra else {}
        kwargs.update(extra)
        response = getattr(self.client, method)(f"{API}{path}", data, **kwargs)
        if response.status_code != expected:
            body = getattr(response, "data", None) or (b"" if response.streaming else response.content[:300])
            self.fail(f"{method.upper()} {path} → {response.status_code} (se esperaba {expected}): {body}")
        return response

    def test_un_comercio_nuevo_de_punta_a_punta(self):
        # 1. Registro y login (sin comercio todavía: el frontend lo manda a crear uno).
        self.call("post", "/auth/register/", {"email": EMAIL, "password": CLAVE, "first_name": "Ana", "last_name": "Paz"}, expected=201)
        login = self.call("post", "/auth/login/", {"email": EMAIL, "password": CLAVE})
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        self.assertIsNone(login.data["user"]["team"])
        no_team = self.call("get", "/orders/", expected=403)
        self.assertEqual(no_team.data["code"], "no_team")

        # 2. Crea su comercio y carga los datos con los que sale el remitente.
        created = self.call("post", "/team/create/", {"name": "Ana Indumentaria"})
        self.assertEqual(created.data["user"]["team"]["role"], "owner")
        self.call("patch", "/auth/me/account/", {
            "legal_name": "Ana Paz", "tax_id": "30536259194", "sender_name": "Ana Indumentaria",
            "sender_address": "Av. Corrientes 1234, CABA", "sender_phone": "1140001000",
        })

        # 3. Suma su tienda y sube la planilla de ventas: 3 pedidos, uno con dos productos.
        store = self.call("post", "/integrations/empretienda/connect/", {"store_url": "ana.empretienda.com.ar", "name": "Ana"}, expected=201).data
        rows = [
            row("101", Nombre="María", Apellido="Gómez"),
            row("101", product="Pantalón", Nombre="María", Apellido="Gómez"),
            row("102", Nombre="Juan", Apellido="Pérez", Ciudad="Rosario", Provincia="Santa Fe", **{"Código postal": "2000"}),
            row("103", Nombre="Lucía", Apellido="Ruiz", Ciudad="Mendoza", Provincia="Mendoza", **{"Código postal": "5500"}),
        ]
        preview = self.call("post", "/integrations/empretienda/import/preview/", {"store": store["id"], "file": csv_file(rows)}, format="multipart")
        self.assertEqual(len(preview.data["orders"]), 3, preview.data)
        self.call("post", "/integrations/empretienda/import/confirm/", {"store": store["id"], "file": csv_file(rows)}, format="multipart")

        orders = self.call("get", f"/orders/?store={store['id']}").data["results"]
        self.assertEqual(sorted(o["external_number"] for o in orders), ["101", "102", "103"])
        ids = sorted(o["id"] for o in orders)

        # 4. Imprime los rótulos de los tres: un PDF de tres páginas con el remitente del comercio.
        batch = self.call("post", "/labels/batch/", {"order_ids": ids}, expected=202).data
        document = batch.get("document", batch)
        self.assertEqual(document["status"], "ready", batch)
        pages, text = pdf_text(self.call("get", f"/documents/{document['id']}/download/"))
        self.assertEqual(pages, 3)
        for name in ("María Gómez", "Juan Pérez", "Lucía Ruiz", "Ana Indumentaria"):
            self.assertIn(name, text)
        self.assertNotIn("maria@example.com", text)  # nunca el contacto del comprador

        # 5. Conecta su cuenta de Andreani y queda lista para despachar.
        self.call("put", "/carriers/accounts/andreani/", {
            "environment": "qa", "username": USER, "password": PASSWORD, "client_code": "CL0001",
            "contracts": [{"code": "400006709", "label": "Domicilio", "kind": "home"}],
            "sender_name": "Ana Indumentaria", "origin_street": "Av. Corrientes", "origin_number": "1234",
            "origin_postal_code": "1043", "origin_city": "CABA",
        }, expected=201)
        andreani = next(c for c in self.call("get", "/carriers/").data["results"] if c["code"] == "andreani")
        self.assertTrue(andreani["account"]["ready"], andreani)

        # 6. Cotiza y despacha los tres: cada pedido queda en preparación con su seguimiento.
        quote = self.call("post", "/carriers/shipments/quote/", {"carrier": "andreani", "order_ids": ids, "contract": "400006709"}).data
        self.assertGreater(float(quote["total"]), 0, quote)
        created = self.call("post", "/carriers/shipments/", {"carrier": "andreani", "order_ids": ids, "contract": "400006709"}).data
        self.assertEqual((created["created"], created["failed"]), (3, 0), created)
        shipments = created["results"]
        for order in Order.objects.filter(pk__in=ids):
            self.assertEqual(order.status, Order.Status.PREPARING)
            self.assertEqual(order.carrier, "Andreani")
            self.assertTrue(order.tracking_number)

        # 7. Imprime rótulo + etiqueta de Andreani de cada envío, y la planilla que firma el chofer.
        pages, text = pdf_text(self.call("post", "/carriers/shipments/print/", {"shipment_ids": [s["id"] for s in shipments]}))
        self.assertGreaterEqual(pages, 6)
        manifest = self.call("post", "/orders/manifest/", {"order_ids": ids, "carrier": "Andreani"})
        _, text = pdf_text(manifest)
        for order in Order.objects.filter(pk__in=ids):
            self.assertIn(order.tracking_number, text)
        self.assertIn("30-53625919-4", text)

        # 8. Andreani informa el avance y el pedido lo sigue, siempre hacia adelante.
        first = shipments[0]
        order = Order.objects.get(pk=first["order_id"])
        steps = [
            (event("Admision", "2026-10-30T10:00:00.000"), Order.Status.DISPATCHED),
            (event("EnvioDespachado", "2026-10-30T20:00:00.000"), Order.Status.IN_TRANSIT),
            (event("EnvioEntregado", "2026-10-31T15:30:00.000", Motivo="Entregado", Estado="Entregado"), Order.Status.DELIVERED),
        ]
        for trace, expected in steps:
            self.andreani.traces.setdefault(first["tracking_number"], []).append(trace)
            self.call("post", f"/carriers/shipments/{first['id']}/refresh/")
            order.refresh_from_db()
            self.assertEqual(order.status, expected)

        # 9. Exporta lo que hizo; la actividad del comercio queda registrada.
        export = self.call("get", f"/orders/export/?file_type=csv&store={store['id']}")
        self.assertEqual(export["X-Order-Count"], "3")
        activity = self.call("get", "/team/activity/").data
        actions = {item["action"] for item in activity.get("results", activity)}
        self.assertTrue({"team.create", "account.update", "store.connect", "order.import", "label.batch", "order.ship"} <= actions, actions)
        # El único mail del recorrido es el de verificación de la cuenta.
        self.assertEqual([message.to for message in mail.outbox], [[EMAIL]])
