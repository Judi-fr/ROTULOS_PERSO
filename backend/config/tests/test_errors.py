"""Errores de la API en JSON (config/errors.py)."""

from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import path
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from config import urls as project_urls


class _BoomView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        raise RuntimeError("algo se rompió adentro")


def _boom_page(request):
    raise RuntimeError("fuera de la API")


def _ok(request):
    return HttpResponse("ok")


# Las rutas reales más dos que explotan, una dentro y otra fuera de /api/.
urlpatterns = project_urls.urlpatterns + [
    path("api/v1/test-boom/", _BoomView.as_view()),
    path("test-boom/", _boom_page),
    path("test-ok/", _ok),
]
handler404 = project_urls.handler404
handler500 = project_urls.handler500


@override_settings(ROOT_URLCONF="config.tests.test_errors", DEBUG=False)
class ApiErrorTests(TestCase):
    def test_excepcion_en_la_api_sale_como_500_json_con_codigo(self):
        with self.assertLogs("apps.errors", level="ERROR") as logs:
            resp = self.client.get("/api/v1/test-boom/")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp["Content-Type"], "application/json")
        body = resp.json()
        self.assertEqual(len(body["error_id"]), 12)
        self.assertIn(body["error_id"], body["detail"])
        # El log trae el mismo código y la traza, para encontrarlo.
        self.assertIn(body["error_id"], logs.output[0])
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn("algo se rompió", body["detail"], "el mensaje interno no se le muestra al cliente")

    def test_ruta_inexistente_de_la_api_404_json(self):
        resp = self.client.get("/api/v1/no-existe/")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json(), {"detail": "Esa ruta de la API no existe."})

    def test_fuera_de_la_api_siguen_las_paginas_de_django(self):
        resp = self.client.get("/no-existe/")
        self.assertEqual(resp.status_code, 404)
        self.assertNotEqual(resp.get("Content-Type"), "application/json")
        self.client.raise_request_exception = False
        resp = self.client.get("/test-boom/")
        self.assertEqual(resp.status_code, 500)
        self.assertNotEqual(resp.get("Content-Type"), "application/json")

    def test_los_errores_de_drf_no_cambian(self):
        resp = self.client.get("/api/v1/auth/me/")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("detail", resp.json())
        self.assertNotIn("error_id", resp.json())

    @override_settings(DEBUG=True)
    def test_con_debug_la_excepcion_sigue_su_camino(self):
        with self.assertRaises(RuntimeError):
            self.client.get("/api/v1/test-boom/")
