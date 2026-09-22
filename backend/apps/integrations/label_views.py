"""Endpoints públicos de los rótulos que pide la tienda (ver ``store_labels``).

Los llama Tiendanube, no el frontend: no hay JWT ni permisos de rol acá, la
tienda se identifica con el token firmado de la URL. Por eso viven en su
propio módulo y se montan aparte (``label_urls``), igual que ``ingest_urls``.

Las rutas NO llevan barra final: la plataforma arma cada URL pegándole un
sufijo (``/generate``, ``/cancel``, ``/suspension``, ``/reactivate``) a la
base que registramos, y un redirect de Django rompería el POST.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.conf import settings
from django.http import FileResponse, Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import store_labels
from .models import StoreConnection, StoreLabelRequest

logger = logging.getLogger(__name__)

TIENDANUBE = StoreConnection.Platform.TIENDANUBE


def _json_body(request):
    """Cuerpo del request ya parseado, o ``None`` si no es JSON válido."""
    try:
        return json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


class StoreLabelCallbackView(APIView):
    """Base de los callbacks: resuelve la tienda a partir del token de la URL.

    Un token inválido responde 404 y no cuenta por qué (no confirma si esa
    tienda existe).
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    # Scope propio: estas llamadas son de la plataforma, no de un visitante
    # anónimo, y un lote masivo entra de golpe.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "store_labels"
    platform = TIENDANUBE

    def get_connection(self, token):
        connection = store_labels.read_callback_token(token, self.platform)
        if connection is None:
            raise Http404
        if connection.status == StoreConnection.Status.REVOKED:
            # La tienda desinstaló la app: no generamos nada más para ella.
            raise Http404
        return connection


class StoreLabelGenerateView(StoreLabelCallbackView):
    """POST /api/v1/integrations/tiendanube/labels/<token>/generate

    El comerciante pidió etiquetas desde el admin de su tienda (masivo o
    individual: es el mismo endpoint, cambia cuántas trae el array).

    Solo acusa recibo: la plataforma corta a los 5 segundos, así que acá se
    guarda y se encola, y el rótulo lo dibuja el worker. 202 = todas
    aceptadas; 207 = una respuesta por etiqueta cuando alguna no se pudo ni
    leer.
    """

    def post(self, request, token):
        connection = self.get_connection(token)
        items = _json_body(request)
        if items is None:
            return Response(
                {"detail": "El payload debe ser JSON válido."}, status=status.HTTP_400_BAD_REQUEST
            )
        if isinstance(items, dict):
            # Tolerancia: una sola etiqueta sin envolver en un array.
            items = [items]
        if not isinstance(items, list) or not items:
            return Response(
                {"detail": "El payload debe ser una lista de etiquetas."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = store_labels.intake(connection, items)
        if all(not error for _label_id, error in results):
            return Response(status=status.HTTP_202_ACCEPTED)

        # 207: la plataforma marca IN_PROGRESS las que digan OK y falla el
        # resto con el motivo que le devolvamos.
        body = []
        for label_id, error in results:
            if error:
                body.append(
                    {
                        "id": label_id,
                        "status": "FAILED",
                        "reason": {"type": store_labels.PLATFORM_REASON_TYPE, "message": error[:300]},
                    }
                )
            else:
                body.append({"id": label_id, "status": "OK"})
        return Response(body, status=status.HTTP_207_MULTI_STATUS)


class StoreLabelDecisionView(StoreLabelCallbackView):
    """Base de las operaciones que la plataforma DECIDE sobre un rótulo ya
    emitido: cancelar, suspender y reactivar.

    Las tres tienen el mismo contrato (``{"labels": [{"fulfillment_order_id",
    "label_id"}]}`` y un 2xx si se aprueban) y de nuestro lado son lo mismo:
    anotar el estado y dejar de servir el PDF. No hay nada que darle de baja
    en un courier, así que se aprueban siempre y nunca se devuelve 207.
    """

    #: Función de ``store_labels`` que aplica la decisión.
    apply = None

    def post(self, request, token):
        connection = self.get_connection(token)
        body = _json_body(request)
        if not isinstance(body, dict):
            return Response(
                {"detail": "El payload debe ser un objeto."}, status=status.HTTP_400_BAD_REQUEST
            )

        pairs = []
        for item in body.get("labels") or []:
            if not isinstance(item, dict):
                continue
            label_id = str(item.get("label_id") or "").strip()
            if label_id:
                pairs.append((str(item.get("fulfillment_order_id") or "").strip(), label_id))

        type(self).apply(connection, pairs)
        return Response(status=status.HTTP_204_NO_CONTENT)


class StoreLabelCancelView(StoreLabelDecisionView):
    """POST /api/v1/integrations/tiendanube/labels/<token>/cancel

    Obligatoria: Tiendanube exige que un carrier implemente ``/generate`` y
    ``/cancel``."""

    apply = staticmethod(store_labels.cancel)


class StoreLabelSuspensionView(StoreLabelDecisionView):
    """POST /api/v1/integrations/tiendanube/labels/<token>/suspension

    Opcional en la plataforma. Suspender es como cancelar pero reversible
    (ver ``StoreLabelReactivateView``)."""

    apply = staticmethod(store_labels.suspend)


class StoreLabelReactivateView(StoreLabelDecisionView):
    """POST /api/v1/integrations/tiendanube/labels/<token>/reactivate

    Opcional en la plataforma. Devuelve a "generado" un rótulo suspendido;
    el PDF no se vuelve a publicar porque a esta altura lo tiene ella."""

    apply = staticmethod(store_labels.reactivate)


class StoreLabelDownloadView(APIView):
    """GET /api/v1/integrations/tiendanube/labels/download/<token>

    Se lo baja la plataforma, sin sesión. El token es la única llave: es
    aleatorio, vive en una sola fila y se invalida apenas la plataforma
    confirma que ya tiene el archivo. Un token consumido o inexistente es
    un 404 igual que cualquier otro.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "store_labels"

    def get(self, request, token):
        # A propósito NO se exige el estado "ready": la plataforma se baja el
        # PDF a partir del aviso que le mandamos, que puede llegarle antes de
        # que terminemos de guardar ese estado.
        max_age = getattr(settings, "STORE_LABEL_DOWNLOAD_MAX_AGE_SECONDS", 86400)
        label_request = (
            StoreLabelRequest.objects.filter(download_token=token)
            .exclude(download_token="")
            .filter(created_at__gte=timezone.now() - timedelta(seconds=max_age))
            .first()
        )
        if label_request is None or not label_request.file:
            raise Http404
        return FileResponse(
            label_request.file.open("rb"),
            content_type="application/pdf",
            as_attachment=True,
            filename=label_request.file.name.rsplit("/", 1)[-1],
        )
