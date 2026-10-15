"""Contrato OpenAPI de la API de ingesta (apps.integrations.ingest_openapi)."""

from django.test import TestCase

from apps.orders.ingestion import TARGET_FIELDS


class IngestOpenApiTests(TestCase):
    URL = "/api/v1/ingest/openapi.json"

    def setUp(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.spec = resp.json()

    def test_es_publico_y_describe_las_dos_entradas(self):
        self.assertEqual(self.spec["openapi"], "3.0.3")
        self.assertEqual(set(self.spec["paths"]), {"/orders/", "/webhooks/{slug}/"})
        self.assertTrue(self.spec["servers"][0]["url"].endswith("/api/v1/ingest"))

    def test_los_campos_son_los_que_valida_la_api(self):
        order = self.spec["components"]["schemas"]["Order"]
        self.assertEqual(set(order["properties"]), set(TARGET_FIELDS))
        self.assertEqual(
            set(order["required"]), {key for key, meta in TARGET_FIELDS.items() if meta["required"]}
        )

    def test_las_rutas_documentadas_existen(self):
        # Un POST sin credenciales tiene que dar 401 (o 404 para un slug
        # inexistente), nunca 404 de ruta: lo documentado es lo que hay.
        self.assertEqual(self.client.post("/api/v1/ingest/orders/", {}, content_type="application/json").status_code, 401)
        resp = self.client.post("/api/v1/ingest/webhooks/no-existe/", {}, content_type="application/json")
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn("Esa ruta de la API no existe", resp.content.decode())

    def test_el_ejemplo_de_pedido_pasa_la_validacion(self):
        from apps.orders.ingestion import validate_mapped_row

        example = self.spec["paths"]["/orders/"]["post"]["requestBody"]["content"]["application/json"]["examples"]["uno"]["value"]
        self.assertEqual(validate_mapped_row(example), [])

    def test_solo_get(self):
        self.assertEqual(self.client.post(self.URL).status_code, 405)
