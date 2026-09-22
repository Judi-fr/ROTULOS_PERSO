"""Tests de los rótulos que pide la tienda desde su propio admin
(``store_labels``): callback de creación, generación en el worker, descarga
pública, cancelación y vencimiento."""

from datetime import timedelta

from django.core import signing
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from apps.labels.label_rendering import build_shipment_context
from apps.labels.models import LabelTemplate

from .. import store_labels
from ..events import process_due_events
from ..models import IntegrationEvent, StoreConnection, StoreLabelRequest
from ..providers import get_provider
from ..stores import GENERATE_LABEL_EVENT
from .test_tiendanube_oauth import TEST_SETTINGS, auth_headers_for, make_user
from .test_tiendanube_sync import TiendanubeTestCase

PUBLIC_BASE_URL = "https://rotulos.example.com"
LABEL_SETTINGS = dict(TEST_SETTINGS, INTEGRATIONS_PUBLIC_BASE_URL=PUBLIC_BASE_URL)

LABEL_ID = "01GSB6KZXT0RTBA0CD4M4WXBSR"
FULFILLMENT_ORDER_ID = "01K1XNB7ZYBV24G3K14YV3NK9E"
LABEL_API_PATH = f"fulfillment-orders/{FULFILLMENT_ORDER_ID}/labels/{LABEL_ID}"


def extract_results(data):
    """Filas de una respuesta paginada o de una lista pelada."""
    return data["results"] if isinstance(data, dict) and "results" in data else data


def extract_first(data):
    return extract_results(data)[0]


def label_callback_item(**overrides):
    """Un elemento del array que Tiendanube manda a ``/generate``."""
    item = {
        "id": LABEL_ID,
        "status": "STARTED",
        "requested_by": {"app_id": "9999", "user_id": "1"},
        "documents": [],
        "created_at": "2026-09-20T17:44:51.364Z",
        "fulfillment_order_info": {
            "id": FULFILLMENT_ORDER_ID,
            "number": "1001",
            "created_at": "2026-09-20T17:44:25.855Z",
            "status": "UNPACKED",
            "recipient": {
                "name": "Juan Pérez",
                "phone": "+54 11 5555-0000",
                "email": "juan@example.com",
            },
            "destination": {
                "zipcode": "1425",
                "street": "Av. Siempre Viva",
                "number": "742",
                "floor": "3B",
                "locality": "Palermo",
                "city": "CABA",
                "province": {"code": "C", "name": "Capital Federal"},
                "country": {"code": "AR", "name": "Argentina"},
            },
            "shipping": {
                "type": "ship",
                "carrier": {"carrier_id": "116425", "code": "api", "app_id": "9999"},
                "option": {"name": "Envío a domicilio", "code": "SEND"},
            },
        },
    }
    item.update(overrides)
    return item


@override_settings(**LABEL_SETTINGS)
class StoreLabelTestCase(TiendanubeTestCase):
    def setUp(self):
        super().setUp()
        # El throttle cuenta en la cache, que sobrevive de un test al otro.
        cache.clear()
        self.addCleanup(cache.clear)
        self.token = store_labels.make_callback_token(self.connection)
        self.generate_url = f"/api/v1/integrations/tiendanube/labels/{self.token}/generate"
        self.cancel_url = f"/api/v1/integrations/tiendanube/labels/{self.token}/cancel"
        # La respuesta al PATCH de estado no se usa, pero sin ruta cargada
        # la API falsa contesta 404.
        self.api.set("PATCH", LABEL_API_PATH, 200, {"id": LABEL_ID})

    def _generate(self, items=None):
        return self.client.post(
            self.generate_url,
            data=items if items is not None else [label_callback_item()],
            format="json",
        )

    def _patches(self):
        return self.api.calls_to("PATCH", LABEL_API_PATH)


class StoreLabelCallbackTests(StoreLabelTestCase):
    def test_el_callback_guarda_el_pedido_de_rotulo_y_lo_encola(self):
        response = self._generate()

        self.assertEqual(response.status_code, 202, response.data)
        label_request = StoreLabelRequest.objects.get()
        self.assertEqual(label_request.connection, self.connection)
        self.assertEqual(label_request.external_label_id, LABEL_ID)
        self.assertEqual(label_request.external_fulfillment_order_id, FULFILLMENT_ORDER_ID)
        self.assertEqual(label_request.status, StoreLabelRequest.Status.PENDING)

        event = IntegrationEvent.objects.get(event_type=GENERATE_LABEL_EVENT)
        self.assertEqual(event.payload, {"label_request_id": label_request.pk})
        self.assertEqual(event.connection, self.connection)

    def test_el_callback_no_dibuja_nada_todavia(self):
        # Tiendanube corta a los 5 segundos: el PDF lo hace el worker.
        self._generate()

        self.assertFalse(StoreLabelRequest.objects.get().file)
        self.assertEqual(self._patches(), [])

    def test_un_pedido_masivo_encola_uno_por_etiqueta(self):
        second = label_callback_item(id="01GSB6KZXT0RTBA0CD4M4WXBST")

        response = self._generate([label_callback_item(), second])

        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual(StoreLabelRequest.objects.count(), 2)
        self.assertEqual(IntegrationEvent.objects.filter(event_type=GENERATE_LABEL_EVENT).count(), 2)

    def test_el_callback_repetido_no_duplica_el_rotulo(self):
        self._generate()
        self._generate()

        self.assertEqual(StoreLabelRequest.objects.count(), 1)

    def test_una_etiqueta_sin_id_se_rechaza_sin_frenar_al_resto(self):
        response = self._generate([{"status": "STARTED"}, label_callback_item()])

        self.assertEqual(response.status_code, 207, response.data)
        self.assertEqual(response.data[0]["status"], "FAILED")
        self.assertEqual(response.data[1], {"id": LABEL_ID, "status": "OK"})
        self.assertEqual(StoreLabelRequest.objects.count(), 1)

    def test_un_token_alterado_no_dice_de_que_tienda_es(self):
        url = f"/api/v1/integrations/tiendanube/labels/{self.token}x/generate"

        response = self.client.post(url, data=[label_callback_item()], format="json")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(StoreLabelRequest.objects.exists())

    def test_una_tienda_desconectada_no_genera_mas_rotulos(self):
        self.connection.status = StoreConnection.Status.REVOKED
        self.connection.save(update_fields=["status"])

        response = self._generate()

        self.assertEqual(response.status_code, 404)
        self.assertFalse(StoreLabelRequest.objects.exists())

    def test_el_token_de_otra_plataforma_no_sirve(self):
        token = signing.dumps({"c": self.connection.pk, "p": "shopify"}, salt=store_labels.CALLBACK_SALT)

        self.assertIsNone(store_labels.read_callback_token(token, "tiendanube"))


class StoreLabelGenerationTests(StoreLabelTestCase):
    def _run_worker(self):
        return process_due_events()

    def test_el_worker_genera_el_pdf_y_avisa_donde_bajarlo(self):
        self._generate()

        self._run_worker()

        label_request = StoreLabelRequest.objects.get()
        self.assertEqual(label_request.status, StoreLabelRequest.Status.READY)
        self.assertTrue(label_request.file)
        self.assertTrue(label_request.download_token)

        patch_call = self._patches()[0]
        self.assertEqual(patch_call["json"]["status"], "READY_TO_DOWNLOAD")
        document = patch_call["json"]["documents"][0]
        self.assertEqual(document["type"], "LABEL")
        self.assertEqual(document["format"], "PDF")
        self.assertEqual(document["download_url_from_app"], store_labels.download_url(label_request))
        self.assertTrue(document["download_url_from_app"].startswith(PUBLIC_BASE_URL))
        self.assertGreater(document["size"], 0)

    def test_el_rotulo_se_dibuja_con_el_remitente_de_la_tienda(self):
        self.connection.sender_name = "Mi Tienda SRL"
        self.connection.sender_address = "Corrientes 1234"
        self.connection.save(update_fields=["sender_name", "sender_address"])
        self._generate()

        self._run_worker()

        shipment = get_provider("tiendanube").normalize_label_request(
            StoreLabelRequest.objects.get().payload
        )
        context = build_shipment_context(shipment, store=self.connection)
        self.assertEqual(context["remitente"], "Mi Tienda SRL")
        self.assertEqual(context["remitente_domicilio"], "Corrientes 1234")
        self.assertEqual(context["destinatario"], "Juan Pérez")
        self.assertEqual(context["domicilio"], "Av. Siempre Viva 742")
        self.assertEqual(context["localidad"], "CABA, Capital Federal")
        self.assertEqual(context["cp"], "1425")
        self.assertEqual(context["pedido"], "Pedido #1001")

    def test_el_rotulo_no_lleva_datos_del_comprador_que_no_sean_del_envio(self):
        # Un rótulo va pegado afuera del paquete: nunca email ni teléfono.
        self._generate()
        shipment = get_provider("tiendanube").normalize_label_request(
            StoreLabelRequest.objects.get().payload
        )

        context = build_shipment_context(shipment, store=self.connection)

        self.assertNotIn("juan@example.com", " ".join(str(v) for v in context.values()))
        self.assertNotIn("5555-0000", " ".join(str(v) for v in context.values()))

    def test_reintentar_despues_de_generar_no_vuelve_a_dibujar(self):
        self._generate()
        self._run_worker()
        label_request = StoreLabelRequest.objects.get()
        first_name = label_request.file.name
        label_request.status = StoreLabelRequest.Status.PENDING
        label_request.save(update_fields=["status"])

        store_labels.generate(label_request)

        self.assertEqual(StoreLabelRequest.objects.get().file.name, first_name)

    def test_sin_plantilla_disponible_se_informa_el_fallo_y_no_se_reintenta(self):
        LabelTemplate.objects.update(is_active=False)
        self._generate()

        self._run_worker()

        label_request = StoreLabelRequest.objects.get()
        self.assertEqual(label_request.status, StoreLabelRequest.Status.FAILED)
        self.assertIn("plantilla", label_request.error_message)
        self.assertEqual(self._patches()[0]["json"]["status"], "FAILED")
        self.assertEqual(self._patches()[0]["json"]["reason"]["type"], "OTHER_ERROR")

        event = IntegrationEvent.objects.get(event_type=GENERATE_LABEL_EVENT)
        self.assertEqual(event.status, IntegrationEvent.Status.FAILED)
        self.assertEqual(event.attempts, 1)

    @override_settings(**dict(LABEL_SETTINGS, INTEGRATIONS_PUBLIC_BASE_URL=""))
    def test_sin_url_publica_el_rotulo_falla_con_motivo(self):
        self._generate()

        self._run_worker()

        label_request = StoreLabelRequest.objects.get()
        self.assertEqual(label_request.status, StoreLabelRequest.Status.FAILED)
        self.assertIn("INTEGRATIONS_PUBLIC_BASE_URL", label_request.error_message)


class StoreLabelDownloadTests(StoreLabelTestCase):
    def _ready_label(self):
        self._generate()
        process_due_events()
        return StoreLabelRequest.objects.get()

    def _download(self, token):
        return self.client.get(f"/api/v1/integrations/tiendanube/labels/download/{token}")

    def test_la_plataforma_baja_el_pdf_sin_sesion(self):
        label_request = self._ready_label()

        response = self._download(label_request.download_token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF"))

    def test_un_token_inexistente_es_404(self):
        self._ready_label()

        self.assertEqual(self._download("no-existe").status_code, 404)

    def test_liberar_la_descarga_deja_de_publicar_el_pdf(self):
        label_request = self._ready_label()
        token = label_request.download_token

        store_labels.release_download(label_request)

        self.assertEqual(self._download(token).status_code, 404)
        self.assertIsNotNone(StoreLabelRequest.objects.get().released_at)

    def test_el_aviso_de_la_plataforma_libera_la_descarga(self):
        label_request = self._ready_label()
        token = label_request.download_token
        self._event(
            "fulfillment_order/label_status_updated",
            resource_id=LABEL_ID,
            payload={"store_id": 555, "id": LABEL_ID, "status": "READY_TO_USE"},
        )

        process_due_events()

        self.assertEqual(self._download(token).status_code, 404)

    def test_un_aviso_sin_estado_no_corta_la_descarga(self):
        label_request = self._ready_label()
        token = label_request.download_token
        self._event(
            "fulfillment_order/label_status_updated",
            resource_id=LABEL_ID,
            payload={"store_id": 555, "id": LABEL_ID},
        )

        process_due_events()

        self.assertEqual(self._download(token).status_code, 200)

    @override_settings(**dict(LABEL_SETTINGS, STORE_LABEL_DOWNLOAD_MAX_AGE_SECONDS=0))
    def test_un_token_vencido_deja_de_servir_el_pdf(self):
        label_request = self._ready_label()

        self.assertEqual(self._download(label_request.download_token).status_code, 404)


class StoreLabelCancelTests(StoreLabelTestCase):
    def test_cancelar_deja_de_publicar_el_pdf(self):
        self._generate()
        process_due_events()
        token = StoreLabelRequest.objects.get().download_token

        response = self.client.post(
            self.cancel_url,
            data={"labels": [{"fulfillment_order_id": FULFILLMENT_ORDER_ID, "label_id": LABEL_ID}]},
            format="json",
        )

        self.assertEqual(response.status_code, 204)
        label_request = StoreLabelRequest.objects.get()
        self.assertEqual(label_request.status, StoreLabelRequest.Status.CANCELED)
        self.assertEqual(label_request.download_token, "")
        self.assertEqual(
            self.client.get(f"/api/v1/integrations/tiendanube/labels/download/{token}").status_code,
            404,
        )

    def test_cancelar_un_rotulo_que_no_tenemos_se_acepta_igual(self):
        response = self.client.post(
            self.cancel_url,
            data={"labels": [{"fulfillment_order_id": "x", "label_id": "no-existe"}]},
            format="json",
        )

        self.assertEqual(response.status_code, 204)

    def test_un_rotulo_cancelado_no_se_vuelve_a_generar(self):
        self._generate()
        StoreLabelRequest.objects.update(status=StoreLabelRequest.Status.CANCELED)

        self._generate()

        self.assertEqual(IntegrationEvent.objects.filter(event_type=GENERATE_LABEL_EVENT).count(), 1)


class StoreLabelExpiryTests(StoreLabelTestCase):
    def test_un_rotulo_colgado_se_informa_como_fallido(self):
        self._generate()
        StoreLabelRequest.objects.update(
            created_at=timezone.now() - timedelta(seconds=3600)
        )

        expired = store_labels.expire_stale_requests()

        self.assertEqual(expired, 1)
        label_request = StoreLabelRequest.objects.get()
        self.assertEqual(label_request.status, StoreLabelRequest.Status.FAILED)
        self.assertIn("plazo", label_request.error_message)
        self.assertEqual(self._patches()[0]["json"]["status"], "FAILED")

    def test_un_rotulo_reciente_no_se_toca(self):
        self._generate()

        self.assertEqual(store_labels.expire_stale_requests(), 0)
        self.assertEqual(
            StoreLabelRequest.objects.get().status, StoreLabelRequest.Status.PENDING
        )

    def test_un_rotulo_ya_generado_no_vence(self):
        self._generate()
        process_due_events()
        StoreLabelRequest.objects.update(created_at=timezone.now() - timedelta(seconds=3600))

        self.assertEqual(store_labels.expire_stale_requests(), 0)


class StoreLabelDecisionTests(StoreLabelTestCase):
    """Suspensión y reactivación: opcionales en Tiendanube, mismo contrato
    que la cancelación."""

    def _ready_label(self):
        self._generate()
        process_due_events()
        return StoreLabelRequest.objects.get()

    def _decide(self, operation):
        return self.client.post(
            f"/api/v1/integrations/tiendanube/labels/{self.token}/{operation}",
            data={"labels": [{"fulfillment_order_id": FULFILLMENT_ORDER_ID, "label_id": LABEL_ID}]},
            format="json",
        )

    def test_suspender_deja_de_publicar_el_pdf(self):
        label_request = self._ready_label()
        token = label_request.download_token

        response = self._decide("suspension")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            StoreLabelRequest.objects.get().status, StoreLabelRequest.Status.SUSPENDED
        )
        self.assertEqual(
            self.client.get(f"/api/v1/integrations/tiendanube/labels/download/{token}").status_code,
            404,
        )

    def test_reactivar_vuelve_a_generado(self):
        self._ready_label()
        self._decide("suspension")

        response = self._decide("reactivate")

        self.assertEqual(response.status_code, 204)
        self.assertEqual(StoreLabelRequest.objects.get().status, StoreLabelRequest.Status.READY)

    def test_las_decisiones_no_avisan_nada_a_la_plataforma(self):
        # Es ella la que pregunta: contestar 2xx ya es la respuesta.
        self._ready_label()
        patches_before = len(self._patches())

        self._decide("suspension")
        self._decide("reactivate")

        self.assertEqual(len(self._patches()), patches_before)

    def test_un_token_alterado_tampoco_sirve_para_suspender(self):
        self._ready_label()

        response = self.client.post(
            f"/api/v1/integrations/tiendanube/labels/{self.token}x/suspension",
            data={"labels": [{"fulfillment_order_id": FULFILLMENT_ORDER_ID, "label_id": LABEL_ID}]},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(StoreLabelRequest.objects.get().status, StoreLabelRequest.Status.READY)


class StoreCarrierTests(StoreLabelTestCase):
    """Alta del medio de envío en la tienda (``register_store_carrier``)."""

    CARRIER_SETTINGS = dict(
        LABEL_SETTINGS,
        STORE_LABEL_RATES_URL="https://rotulos.example.com/api/v1/integrations/tiendanube/rates",
        STORE_LABEL_CARRIER_NAME="Rótulos",
    )

    @override_settings(**CARRIER_SETTINGS)
    def test_se_da_de_alta_con_el_callback_de_rotulos_de_esa_tienda(self):
        self.api.set("GET", "shipping_carriers", 200, [])
        self.api.set("POST", "shipping_carriers", 201, {"id": 4242, "name": "Rótulos"})

        carrier = store_labels.register_carrier(self.connection)

        body = self.api.calls_to("POST", "shipping_carriers")[0]["json"]
        self.assertEqual(body["name"], "Rótulos")
        self.assertEqual(body["callback_labels_url"], store_labels.callback_base_url(self.connection))
        self.assertTrue(body["callback_url"])
        self.assertEqual(carrier["id"], 4242)
        self.connection.refresh_from_db()
        self.assertEqual(
            self.connection.preferences[store_labels.CARRIER_ID_PREFERENCE], "4242"
        )

    @override_settings(**CARRIER_SETTINGS)
    def test_reinstalar_actualiza_el_carrier_en_vez_de_duplicarlo(self):
        self.api.set("GET", "shipping_carriers", 200, [{"id": 99, "name": "Rótulos"}])
        self.api.set("PUT", "shipping_carriers/99", 200, {"id": 99, "name": "Rótulos"})

        store_labels.register_carrier(self.connection)

        self.assertEqual(len(self.api.calls_to("POST", "shipping_carriers")), 0)
        self.assertEqual(len(self.api.calls_to("PUT", "shipping_carriers/99")), 1)

    @override_settings(**dict(LABEL_SETTINGS, STORE_LABEL_RATES_URL=""))
    def test_sin_endpoint_de_cotizacion_no_se_da_de_alta(self):
        # Tiendanube lo exige junto con el de rótulos: darlo de alta sin eso
        # le dejaría a la tienda un medio de envío que no contesta.
        with self.assertRaises(store_labels.LabelGenerationError) as ctx:
            store_labels.register_carrier(self.connection)

        self.assertIn("STORE_LABEL_RATES_URL", str(ctx.exception))
        self.assertEqual(len(self.api.calls_to("POST", "shipping_carriers")), 0)

    def test_el_alta_no_pasa_sola_al_conectar_la_tienda(self):
        # La conexión ya ocurrió en setUp (ver TiendanubeTestCase).
        process_due_events()

        self.assertEqual(len(self.api.calls_to("POST", "shipping_carriers")), 0)


class StoreLabelApiFeatureTests(StoreLabelTestCase):
    def test_sin_dato_guardado_no_se_afirma_nada(self):
        self.assertIsNone(store_labels.supports_label_api(self.connection))

    def test_el_plan_con_la_feature_habilita_la_api_de_rotulos(self):
        self.connection.preferences = {"features": ["fulfillment_order_label_api", "otra"]}
        self.connection.save(update_fields=["preferences"])

        self.assertIs(store_labels.supports_label_api(self.connection), True)

    def test_un_plan_sin_la_feature_se_marca_como_no(self):
        self.connection.preferences = {"features": ["otra"]}
        self.connection.save(update_fields=["preferences"])

        self.assertIs(store_labels.supports_label_api(self.connection), False)


class StoreLabelListApiTests(StoreLabelTestCase):
    """``GET /api/v1/integrations/store-labels/``: lo que consume
    ``rotulos_tienda.html`` para que el comerciante vea por qué falló un
    rótulo."""

    URL = "/api/v1/integrations/store-labels/"

    def setUp(self):
        super().setUp()
        self._generate()
        self.label_request = StoreLabelRequest.objects.get()

    def test_requiere_estar_logueado(self):
        self.assertEqual(self.client.get(self.URL).status_code, 401)

    def test_el_dueno_ve_los_rotulos_de_su_tienda_con_el_motivo_del_fallo(self):
        store_labels.fail(self.label_request, "No hay ninguna plantilla pública disponible.")

        response = self.client.get(self.URL, **auth_headers_for(self.owner))

        self.assertEqual(response.status_code, 200, response.data)
        row = extract_first(response.data)
        self.assertEqual(row["external_label_id"], LABEL_ID)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["status_label"], "Falló")
        self.assertEqual(row["store_name"], "Tienda de prueba")
        self.assertIn("plantilla", row["error_message"])

    def test_no_se_filtran_los_datos_del_comprador(self):
        # El payload guardado trae nombre, email y domicilio: nada de eso
        # sale por la API (el rótulo sí los usa, la pantalla no los necesita).
        response = self.client.get(self.URL, **auth_headers_for(self.owner))

        body = str(response.data)
        self.assertNotIn("payload", body)
        self.assertNotIn("juan@example.com", body)
        self.assertNotIn("Av. Siempre Viva", body)
        self.assertNotIn("download_token", body)

    def test_un_usuario_no_ve_los_rotulos_de_la_tienda_de_otro(self):
        intruso = make_user("otro@example.com")

        response = self.client.get(self.URL, **auth_headers_for(intruso))

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(extract_results(response.data)), 0)

    def test_se_puede_filtrar_por_estado(self):
        response = self.client.get(
            self.URL, {"status": "failed"}, **auth_headers_for(self.owner)
        )

        self.assertEqual(len(extract_results(response.data)), 0)

    def test_se_puede_filtrar_por_tienda(self):
        response = self.client.get(
            self.URL, {"store": self.connection.pk}, **auth_headers_for(self.owner)
        )

        self.assertEqual(len(extract_results(response.data)), 1)

    def test_un_estado_inventado_no_recorta_la_lista(self):
        response = self.client.get(
            self.URL, {"status": "cualquiera"}, **auth_headers_for(self.owner)
        )

        self.assertEqual(len(extract_results(response.data)), 1)

    def test_la_tienda_informa_si_su_plan_permite_la_api_de_rotulos(self):
        self.connection.preferences = {"features": ["fulfillment_order_label_api"]}
        self.connection.save(update_fields=["preferences"])

        response = self.client.get(
            "/api/v1/integrations/stores/", **auth_headers_for(self.owner)
        )

        self.assertIs(extract_first(response.data)["label_api_enabled"], True)
