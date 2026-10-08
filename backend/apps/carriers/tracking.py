"""Seguimiento automático de los envíos de todos los transportistas.

Lo llama el worker en cada vuelta (``run_integrations_worker``): consulta los
envíos abiertos que hace rato no se miran y actualiza envío y pedido. Una falla
de un envío (o de la cuenta) se anota y no frena a los demás.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from .andreani import shipments as andreani
from .andreani.client import AndreaniAuthError, AndreaniError
from .models import CarrierAccount

logger = logging.getLogger(__name__)


def sync_due_shipments(limit=50):
    """Consulta los envíos que tocan. Devuelve cuántos cambiaron de estado."""
    changed = 0
    for shipment in andreani.due_shipments(limit=limit):
        try:
            changed += int(andreani.sync_shipment(shipment))
        except AndreaniAuthError as exc:
            # Credenciales que ya no sirven: se ve en la cuenta y se deja de
            # insistir hasta que el cliente las corrija.
            CarrierAccount.objects.filter(pk=shipment.account_id).update(is_active=False, last_error=str(exc)[:2000])
        except AndreaniError as exc:
            logger.warning("No se pudo actualizar el envío %s: %s", shipment.tracking_number, exc)
            # Se marca como consultado igual: si no, se reintentaría en cada
            # vuelta del worker, cada pocos segundos.
            type(shipment).objects.filter(pk=shipment.pk).update(last_error=str(exc)[:2000], last_checked_at=timezone.now())
    return changed
