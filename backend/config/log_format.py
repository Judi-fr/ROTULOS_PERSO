"""Formato JSON para los logs (una línea = un objeto JSON).

Se activa con ``LOG_FORMAT=json`` (ver LOGGING en settings/base.py); el
default sigue siendo el texto legible, que es lo cómodo en desarrollo. En un
servidor, JSON es lo que cualquier recolector de logs (Loki, CloudWatch,
Datadog, ``docker logs | jq``) filtra por campo sin expresiones regulares:
``level``, ``logger``, y los extras que mande el código — por ejemplo el
``error_id`` de un 500 de la API (config/errors.py), para encontrar la traza
que cita un comerciante.

Sin dependencias: es un ``logging.Formatter`` de la biblioteca estándar.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

# Atributos que todo LogRecord trae: lo que no está acá es un extra que puso
# el código (``logger.info(..., extra={...})``) y va al JSON.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key in _STANDARD_ATTRS or key.startswith("_") or key in payload:
                continue
            payload[key] = value if isinstance(value, (str, int, float, bool, type(None))) else repr(value)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False)
