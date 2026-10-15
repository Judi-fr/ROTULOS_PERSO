"""Logs en JSON (config/log_format.py) y la variable LOG_FORMAT."""

import json
import logging
import sys

from django.test import SimpleTestCase

from config.log_format import JsonFormatter


class JsonFormatterTests(SimpleTestCase):
    def _record(self, msg, *args, exc_info=None, **extra):
        record = logging.makeLogRecord(
            {"name": "apps.prueba", "levelno": logging.ERROR, "levelname": "ERROR", "msg": msg, "args": args, **extra}
        )
        record.exc_info = exc_info
        return record

    def test_una_linea_json_con_los_campos_basicos_y_los_extras(self):
        line = JsonFormatter().format(self._record("Pedido %s sin CP", 42, error_id="abc123", path="/api/v1/x/"))
        self.assertNotIn("\n", line)
        data = json.loads(line)
        self.assertEqual(data["level"], "ERROR")
        self.assertEqual(data["logger"], "apps.prueba")
        self.assertEqual(data["message"], "Pedido 42 sin CP")
        self.assertEqual(data["error_id"], "abc123")
        self.assertEqual(data["path"], "/api/v1/x/")
        self.assertTrue(data["ts"].endswith("+00:00"))
        self.assertNotIn("args", data)

    def test_la_traza_va_en_un_campo(self):
        try:
            raise ValueError("roto")
        except ValueError:
            line = JsonFormatter().format(self._record("falló", exc_info=sys.exc_info()))
        data = json.loads(line)
        self.assertIn("ValueError: roto", data["exception"])

    def test_extras_no_serializables_no_rompen(self):
        data = json.loads(JsonFormatter().format(self._record("x", objeto={1, 2})))
        self.assertIn("1", data["objeto"])
