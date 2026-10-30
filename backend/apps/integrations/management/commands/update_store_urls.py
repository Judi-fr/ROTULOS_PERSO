"""``python manage.py update_store_urls`` — después de cambiar el dominio
(``INTEGRATIONS_PUBLIC_BASE_URL``), vuelve a apuntar a la URL nueva lo que cada
tienda conectada tiene registrado con la vieja.

- Encola ``internal/store_setup`` de cada tienda activa: el worker registra los
  webhooks que falten con la URL nueva (de paso reimporta los pedidos recientes,
  que es idempotente). Los webhooks viejos no se borran: fallan contra el
  dominio anterior y la plataforma termina desactivándolos.
- En las de Tiendanube con medio de envío dado de alta, actualiza en el momento
  sus dos callbacks (cotización y rótulos) — ``register_carrier`` reutiliza el
  carrier existente, no crea otro.

WooCommerce y Magento además reciben la URL nueva solos en la próxima
reconciliación (``configure_admin_print``). Ver docs/cambio-de-dominio.md.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.integrations.models import StoreConnection
from apps.integrations.providers.base import ProviderError
from apps.integrations.providers.tiendanube import labels as store_labels
from apps.integrations.stores import enqueue_store_setup


class Command(BaseCommand):
    help = "Vuelve a registrar webhooks y medios de envío de las tiendas con el dominio actual."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Solo lista lo que haría.")

    def handle(self, *args, **options):
        base_url = (getattr(settings, "INTEGRATIONS_PUBLIC_BASE_URL", "") or "").rstrip("/")
        if not base_url:
            raise CommandError("Falta INTEGRATIONS_PUBLIC_BASE_URL: es la URL a la que se apunta todo.")
        dry_run = options["dry_run"]
        self.stdout.write(f"URL pública: {base_url}{' (simulación, no cambia nada)' if dry_run else ''}")

        stores = StoreConnection.objects.filter(status=StoreConnection.Status.ACTIVE).order_by("pk")
        failures = 0
        for connection in stores:
            has_carrier = bool((connection.preferences or {}).get(store_labels.CARRIER_ID_PREFERENCE))
            label = f"#{connection.pk} {connection.platform} «{connection.name or connection.external_store_id}»"
            if dry_run:
                self.stdout.write(f"{label}: webhooks{' + medio de envío' if has_carrier else ''}")
                continue

            enqueue_store_setup(connection)
            if not has_carrier:
                self.stdout.write(f"{label}: webhooks encolados")
                continue
            try:
                store_labels.register_carrier(connection)
            except (ProviderError, store_labels.LabelGenerationError) as exc:
                failures += 1
                self.stdout.write(self.style.ERROR(f"{label}: webhooks encolados; medio de envío FALLÓ: {exc}"))
            else:
                self.stdout.write(f"{label}: webhooks encolados y medio de envío actualizado")

        if not dry_run:
            self.stdout.write("Los webhooks se registran cuando corre el worker (run_integrations_worker).")
        if failures:
            raise CommandError(f"{failures} tienda(s) no pudieron actualizar el medio de envío; ver arriba.")
