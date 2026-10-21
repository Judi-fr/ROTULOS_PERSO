"""``python manage.py run_monitor`` — corre los chequeos de ``apps.monitoring``
cada ``MONITORING_INTERVAL_SECONDS`` y manda las alertas por mail.

Va en un proceso aparte del worker a propósito: el chequeo más importante es
justamente "el worker no late", y eso no lo puede avisar el worker. Si este
proceso se cae, el worker lo nota y hace las alertas él (``heartbeats.worker_tick``).
``--once`` hace una pasada y muestra el resultado (para probar a mano o cron).
"""

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.monitoring import heartbeats
from apps.monitoring import notices
from apps.monitoring.alerts import run_alerts

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Chequea el estado del sistema y avisa por mail a los admins cuando algo falla."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Una pasada y termina.")

    def handle(self, *args, **options):
        try:
            while True:
                close_old_connections()
                try:
                    heartbeats.beat(heartbeats.MONITOR, force=True)
                except Exception:  # noqa: BLE001 - con la base caída igual hay que chequear
                    pass
                summary = run_alerts()
                # Los avisos a cada comercio (notices.py): no frenan las alertas.
                try:
                    notices.sync_all()
                except Exception:  # noqa: BLE001
                    logger.exception("Falló la pasada de avisos a los comercios.")
                if options["once"]:
                    for result in summary["results"]:
                        self.stdout.write(f"{result.status:8} {result.label}: {result.detail}")
                    if summary["sent"]:
                        self.stdout.write("Se mandó el mail de alerta.")
                    return
                if summary["sent"]:
                    self.stdout.write(
                        f"Alerta enviada (nuevos {summary['new']}, siguen {summary['reminders']}, "
                        f"resueltos {summary['recovered']})"
                    )
                time.sleep(settings.MONITORING_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            self.stdout.write("Monitor detenido.")
