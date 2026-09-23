"""El callback de cotización que llama la plataforma en cada checkout.

Separado de ``label_views`` a propósito aunque comparta el token: aquello
resuelve rótulos después de la venta y puede tomarse su tiempo en un
worker; esto está en el camino de una venta ajena y tiene que contestar
ya. Ver ``shipping_rates``.
"""

from __future__ import annotations

import json
import logging

from django.http import Http404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import shipping_rates, store_labels
from .models import StoreConnection

logger = logging.getLogger(__name__)

TIENDANUBE = StoreConnection.Platform.TIENDANUBE


class TiendanubeRatesView(APIView):
    """POST /api/v1/integrations/tiendanube/rates/<token>

    Recibe el carrito y devuelve ``{"rates": [...]}``. El token de la URL
    dice de qué tienda es; un token inválido o una tienda que desinstaló la
    app responden 404 sin contar por qué.

    **Nunca devuelve 5xx por un carrito raro.** Un payload que no se puede
    leer se contesta con la lista vacía: para la plataforma eso significa
    "no cotizo este envío" y el checkout sigue con las otras opciones, en
    vez de sumar un error al corta-corriente que nos saca del checkout de
    todas las tiendas.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "store_rates"

    def post(self, request, token):
        connection = store_labels.read_callback_token(token, TIENDANUBE)
        if connection is None or connection.status == StoreConnection.Status.REVOKED:
            raise Http404

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict):
            logger.warning("Carrito ilegible de la tienda %s.", connection.pk)
            return Response({"rates": []})

        try:
            return Response(shipping_rates.quote(connection, payload))
        except Exception:
            # Cotizar es una consulta a la base: si falla, es un bug
            # nuestro. Queda en el log con traza, pero el comprador no se
            # queda sin checkout por eso.
            logger.exception("Error cotizando para la tienda %s.", connection.pk)
            return Response({"rates": []})
