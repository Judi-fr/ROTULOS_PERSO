"""Sondas de salud (``config/health.py``).

Lo que se fija acá es la diferencia entre las dos sondas, que es la razón
de que existan por separado: liveness NO puede depender de la base y
readiness SÍ tiene que fallar cuando la base no está.
"""

from unittest.mock import patch

from django.test import TestCase


class LivenessTests(TestCase):
    URL = "/api/v1/health/"

    def test_responde_ok(self):
        respuesta = self.client.get(self.URL)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json()["status"], "ok")

    def test_no_necesita_autenticacion(self):
        """La consulta el balanceador, que no tiene token ni sesión."""
        self.assertEqual(self.client.get(self.URL).status_code, 200)

    def test_sigue_respondiendo_aunque_la_base_este_caida(self):
        """Si liveness consultara la base, un hipo de Postgres haría que el
        orquestador reinicie la API en loop — justo cuando el problema no
        es la API."""
        with patch("config.health.connections") as conexiones:
            conexiones.__getitem__.side_effect = Exception("base caída")
            respuesta = self.client.get(self.URL)

        self.assertEqual(respuesta.status_code, 200)


class ReadinessTests(TestCase):
    URL = "/api/v1/health/ready/"

    def test_con_la_base_arriba_devuelve_200_y_el_detalle(self):
        respuesta = self.client.get(self.URL)

        self.assertEqual(respuesta.status_code, 200)
        cuerpo = respuesta.json()
        self.assertEqual(cuerpo["status"], "ok")
        self.assertEqual(cuerpo["checks"]["database"], "ok")

    def test_con_la_base_caida_devuelve_503(self):
        """El código de estado es lo que mira un monitor: un fallo tiene que
        ser un 5xx y no un 200 con el problema escondido en el cuerpo. Esto
        es lo que la sonda vieja no hacía: contestaba ok siempre."""
        with patch("config.health.connections") as conexiones:
            conexiones.__getitem__.side_effect = Exception("no se pudo conectar")
            respuesta = self.client.get(self.URL)

        self.assertEqual(respuesta.status_code, 503)
        cuerpo = respuesta.json()
        self.assertEqual(cuerpo["status"], "error")
        self.assertIn("no se pudo conectar", cuerpo["checks"]["database"])

    def test_no_necesita_autenticacion(self):
        self.assertEqual(self.client.get(self.URL).status_code, 200)
