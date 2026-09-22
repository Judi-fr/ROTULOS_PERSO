"""``python manage.py register_store_carrier`` — da de alta nuestro medio de
envío en una tienda conectada, con el callback de rótulos apuntado a ella.

Es un comando y no algo que pase solo al conectar la tienda, a propósito:
Tiendanube exige un ``callback_url`` de COTIZACIÓN junto con el de rótulos,
así que desde que el carrier existe la tienda nos pregunta precios en cada
checkout. Darlo de alta antes de que ese endpoint exista le rompería el
checkout al comerciante (ver ``store_labels.register_carrier``).

Además hace falta que Tiendanube haya habilitado los endpoints de shipping
para la app (formulario del Portal de Partners).
"""

from django.core.management.base import BaseCommand, CommandError

from apps.integrations import store_labels
from apps.integrations.models import StoreConnection
from apps.integrations.providers.base import ProviderError


class Command(BaseCommand):
    help = "Da de alta el medio de envío de la app en una tienda conectada."

    def add_arguments(self, parser):
        parser.add_argument(
            "store_id",
            nargs="?",
            type=int,
            help="Id de la StoreConnection. Sin esto, se listan las tiendas disponibles.",
        )

    def handle(self, *args, **options):
        store_id = options["store_id"]
        if store_id is None:
            self._list_stores()
            return

        connection = StoreConnection.objects.filter(
            pk=store_id, status=StoreConnection.Status.ACTIVE
        ).first()
        if connection is None:
            raise CommandError(f"No hay una tienda conectada con id {store_id}.")

        supports = store_labels.supports_label_api(connection)
        if supports is False:
            self.stdout.write(
                self.style.WARNING(
                    "Ojo: el plan de esta tienda no incluye la API de rótulos "
                    "(fulfillment_order_label_api). El carrier se da de alta igual, "
                    "pero pedir etiquetas va a devolver 403."
                )
            )

        try:
            carrier = store_labels.register_carrier(connection)
        except (ProviderError, store_labels.LabelGenerationError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Medio de envío dado de alta en {connection}: "
                f"id {carrier.get('id')}, nombre {carrier.get('name')!r}."
            )
        )
        self.stdout.write(f"Callback de rótulos: {store_labels.callback_base_url(connection)}")

    def _list_stores(self):
        stores = StoreConnection.objects.filter(status=StoreConnection.Status.ACTIVE)
        if not stores:
            self.stdout.write("No hay tiendas conectadas.")
            return
        self.stdout.write("Tiendas conectadas (pasar el id como argumento):")
        for connection in stores:
            supports = store_labels.supports_label_api(connection)
            label_api = {True: "sí", False: "no", None: "?"}[supports]
            carrier = (connection.preferences or {}).get(store_labels.CARRIER_ID_PREFERENCE) or "-"
            self.stdout.write(
                f"  {connection.pk}\t{connection}\tAPI de rótulos: {label_api}\tcarrier: {carrier}"
            )
