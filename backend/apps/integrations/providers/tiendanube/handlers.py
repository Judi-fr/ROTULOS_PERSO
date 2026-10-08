"""Handlers de la cola propios de Tiendanube: los rótulos que pide la tienda
desde su admin (Labels API, ver ``labels``). Se registran al importar este
módulo, cosa que hace ``apps.integrations.handlers`` al final."""

from __future__ import annotations

from ...events import PermanentEventError, register_handler
from ...handlers import _connection_for
from ...models import StoreConnection, StoreLabelRequest
from ...stores import GENERATE_LABEL_EVENT
from ..base import ProviderAuthError, ProviderNotFoundError, ProviderRejectedError
from . import labels as store_labels
from .provider import LABEL_STATUS_EVENT, TiendanubeProvider

TIENDANUBE = StoreConnection.Platform.TIENDANUBE

# Los que se registran en una tienda Tiendanube (se definen en su proveedor).
WEBHOOK_EVENTS = TiendanubeProvider().webhook_events

# Estados de la plataforma en los que ya tiene el PDF guardado ella: a
# partir de ahí dejamos de publicarlo (ver labels.release_download).
LABEL_RELEASED_STATUSES = ("READY_TO_USE", "DOWNLOADED")


@register_handler(TIENDANUBE, GENERATE_LABEL_EVENT)
def generate_store_label(event):
    """Dibuja el rótulo que pidió la tienda y le avisa dónde bajarlo.

    Un rótulo que no se puede dibujar ya quedó informado como fallido del
    lado de la plataforma (``store_labels.generate``), así que acá solo se
    traduce a un error que la cola no reintenta.
    """
    connection = _connection_for(event, require_owner=False)
    label_request = StoreLabelRequest.objects.filter(
        pk=event.payload.get("label_request_id"), connection=connection
    ).first()
    if label_request is None:
        raise PermanentEventError("El rótulo pedido ya no existe o no es de esta tienda.")

    try:
        store_labels.generate(label_request)
    except store_labels.LabelGenerationError as exc:
        raise PermanentEventError(str(exc)) from exc
    except (ProviderAuthError, ProviderNotFoundError, ProviderRejectedError) as exc:
        # La plataforma rechazó el aviso (etiqueta vencida, cancelada o un
        # token que ya no sirve): reintentar no lo arregla.
        store_labels.fail(label_request, str(exc))
        raise PermanentEventError(str(exc)) from exc


@register_handler(TIENDANUBE, LABEL_STATUS_EVENT)
def label_status_updated(event):
    """La plataforma avisa en qué quedó una etiqueta. Solo nos importa para
    dejar de publicar el PDF: una vez que lo tiene ella, nuestra URL
    pública no tiene por qué seguir existiendo."""
    connection = event.connection
    if connection is None:
        return  # No es una tienda nuestra: no hay nada que liberar.

    payload = event.payload or {}
    label_id = str(payload.get("label_id") or payload.get("id") or event.resource_id or "").strip()
    if not label_id:
        return

    label_request = StoreLabelRequest.objects.filter(
        connection=connection, external_label_id=label_id
    ).first()
    if label_request is None:
        return

    # Solo con un estado conocido: sin estado no se sabe si la plataforma
    # alcanzó a bajar el PDF, y dejar de publicarlo antes de tiempo le
    # rompería la etiqueta al comerciante.
    status = str(payload.get("status") or "").strip().upper()
    if status in LABEL_RELEASED_STATUSES:
        store_labels.release_download(label_request)


