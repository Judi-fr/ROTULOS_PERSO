"""``python manage.py run_integrations_worker`` — procesa la cola de eventos
de tiendas conectadas (``IntegrationEvent``, ver ``apps.integrations.events``).

Corre en un proceso aparte del servidor web (en Docker, un servicio más con
el mismo código). ``--once`` procesa un solo lote y termina (útil para cron
o para probar a mano).
"""

import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.integrations.events import process_due_events
from apps.integrations.store_labels import expire_stale_requests


class Command(BaseCommand):
    help = "Procesa la cola de eventos de tiendas conectadas, con reintentos."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Procesa un lote y termina.")
        parser.add_argument("--limit", type=int, default=50, help="Eventos por lote (default 50).")
        parser.add_argument(
            "--sleep",
            type=float,
            default=5.0,
            help="Segundos de espera cuando no hay eventos pendientes (default 5).",
        )

    def handle(self, *args, **options):
        try:
            while True:
                # Un worker de larga duración no puede quedarse con una
                # conexión a la base que el servidor ya cerró.
                close_old_connections()
                # Antes del lote: los rótulos que la tienda pidió y quedaron
                # pendientes demasiado tiempo se dan por fallidos con motivo,
                # en vez de dejar que la plataforma los venza en silencio.
                expired = expire_stale_requests()
                if expired:
                    self.stdout.write(f"Rótulos vencidos informados como fallidos: {expired}")
                counts = process_due_events(limit=options["limit"])
                processed = sum(counts.values())
                if processed:
                    self.stdout.write(
                        f"Eventos procesados: {processed} "
                        f"(ok {counts['done']}, reintento {counts['pending']}, fallidos {counts['failed']})"
                    )
                if options["once"]:
                    return
                if not processed:
                    time.sleep(options["sleep"])
        except KeyboardInterrupt:
            self.stdout.write("Worker detenido.")
