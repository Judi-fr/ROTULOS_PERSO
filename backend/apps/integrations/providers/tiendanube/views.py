"""Vistas propias de Tiendanube: la impresión desde el link de acciones
masivas de Ventas (``tiendanube/print-link/`` + ``tiendanube/print/<token>``).
Los callbacks de rótulos y de cotización, que llama la plataforma con su
propia autenticación, están en ``label_views``/``rate_views``. La
instalación OAuth y el link para compartir son genéricos
(``apps.integrations.views``).
"""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions_map import user_has_permission
from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record

from ... import store_print
from ...models import StoreConnection
from ...views import _print_document

logger = logging.getLogger(__name__)


class TiendanubePrintLinkView(APIView):
    """POST /api/v1/integrations/tiendanube/print-link/  ``{"store", "ids"}``

    "Imprimir rótulos" en las acciones masivas de Ventas del admin de
    Tiendanube: es un *link de app* configurado en el Portal de Partners, que
    abre ``imprimir_tiendanube.html`` en el navegador del comerciante con los
    pedidos elegidos. Tiendanube no firma ese link, así que la identidad NO
    sale de él: sale del JWT del comerciante logueado en nuestra app, y la
    tienda tiene que ser suya. ``store`` es el id de la tienda en Tiendanube;
    si no llega y el usuario tiene una sola Tiendanube activa, es esa.
    ``ids`` son los ids de pedido de Tiendanube (``external_id``).

    ``action``: ``labels`` (por defecto), ``manifest`` o ``dispatch`` — cada
    una es otro link de acciones masivas que abre su página
    (``imprimir_tiendanube.html``, ``planilla_tiendanube.html``,
    ``despachar_tiendanube.html``). Despachar pide además ``orders.create``."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("labels.batch")]

    def post(self, request):
        stores = StoreConnection.objects.filter(
            platform=StoreConnection.Platform.TIENDANUBE, owner=request.user, status=StoreConnection.Status.ACTIVE
        )
        store_id = str(request.data.get("store") or "").strip()
        if store_id:
            connection = stores.filter(external_store_id=store_id).first()
        else:
            connection = stores.first() if stores.count() == 1 else None
        if connection is None:
            return Response(
                {"detail": "Esa tienda de Tiendanube no está conectada a tu cuenta."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            action = store_print.parse_action(request.data.get("action"))
            if action == store_print.DISPATCH and not user_has_permission(request.user, "orders.create"):
                return Response({"detail": "Tu usuario no puede despachar pedidos."}, status=status.HTTP_403_FORBIDDEN)
            orders, missing = store_print.resolve_orders(connection, store_print.order_ids(request.data.get("ids")))
            if not orders:
                raise store_print.PrintError("Ninguno de los pedidos elegidos existe en la tienda.")
            url, summary = store_print.action_link(connection, orders, action, route="tiendanube-print")
        except store_print.PrintError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        record(
            request,
            category="labels",
            action="label.batch",
            target=connection,
            target_type="storeconnection",
            target_repr=str(connection),
            changes={f"tiendanube_{action}": {"from": None, "to": len(orders)}},
        )
        return Response({"url": url, "count": len(orders), "missing": missing, "action": action, **summary})


def tiendanube_print_document(request, token):
    """GET /api/v1/integrations/tiendanube/print/<token>

    El PDF al que ``imprimir_tiendanube.html`` manda el navegador. Lo
    autentica el enlace firmado y de vida corta; sin sesión, sin JWT."""
    return _print_document(request, token, StoreConnection.Platform.TIENDANUBE)
