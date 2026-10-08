"""Vistas de Empretienda: agregar la tienda e importar su planilla de ventas.

- ``POST empretienda/connect/`` ``{store_url, name}`` (``orders.create``): la
  tienda, sin credenciales (no hay API). Reconectarla con la misma dirección
  reactiva la misma fila.
- ``POST empretienda/import/preview/`` (multipart: ``store``, ``file``,
  ``mapping`` JSON opcional; ``orders.create``): qué columna se tomó para cada
  dato y cómo quedan los pedidos. No guarda nada.
- ``POST empretienda/import/confirm/`` (lo mismo): guarda los pedidos válidos y
  el mapeo usado, para la próxima vez.

El archivo se manda en los dos pasos (como en la importación de seguimientos):
así no hay que guardar en el servidor una planilla con datos de compradores
entre un paso y otro.
"""

from __future__ import annotations

import json

from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record
from apps.orders.import_parsing import ImportFileError, parse_tabular_file
from apps.orders.ingestion import upsert_store_order
from apps.orders.models import Order

from ...models import StoreConnection
from ...serializers import StoreConnectionSerializer
from ...stores import connect_with_api_credentials
from ...views import STORE_CONNECT_PERMISSION, _record_store_connect
from ...webhooks import dispatch_event, webhooks_suspended
from .. import get_provider
from . import spreadsheet
from .provider import external_store_id_for

# La planilla es cómo entran los pedidos de SU tienda (en las demás
# plataformas eso pasa solo): el mismo permiso que conectar una tienda, no el
# ``orders.import`` de la importación genérica, que los comerciantes no tienen.
IMPORT_PERMISSION = STORE_CONNECT_PERMISSION
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000


class EmpretiendaConnectView(APIView):
    """POST /api/v1/integrations/empretienda/connect/ ``{"store_url", "name"}``"""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(STORE_CONNECT_PERMISSION)]

    def post(self, request):
        provider = get_provider(StoreConnection.Platform.EMPRETIENDA)
        data = request.data if isinstance(request.data, dict) else {}
        try:
            store_url = provider.normalize_shop_domain(data.get("store_url", ""))
        except ValueError as exc:
            return Response({"store_url": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        name = str(data.get("name") or "").strip()[:150]

        result = connect_with_api_credentials(
            provider, request.user, external_store_id=external_store_id_for(store_url), store_url=store_url, token=""
        )
        if result.owner_conflict:
            return Response(
                {"detail": "Esa tienda ya está cargada en otra cuenta. Si es tuya, escribinos desde Ayuda / Soporte."},
                status=status.HTTP_409_CONFLICT,
            )
        connection = result.connection
        if name and connection.name != name:
            connection.name = name
            connection.save(update_fields=["name", "updated_at"])
        _record_store_connect(request, request.user, connection)
        return Response(
            StoreConnectionSerializer(connection, context={"request": request}).data,
            status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK,
        )


class _SpreadsheetView(APIView):
    """Lo común a la vista previa y a la confirmación: la tienda, el archivo
    leído y el mapeo de columnas."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(IMPORT_PERMISSION)]

    def _load(self, request):
        """``(connection, headers, rows, mapping)`` o una ``Response`` de error."""
        store = str(request.data.get("store") or "").strip()
        connection = StoreConnection.objects.filter(
            pk=int(store) if store.isdigit() else None,
            owner=request.user,
            platform=StoreConnection.Platform.EMPRETIENDA,
            status=StoreConnection.Status.ACTIVE,
        ).first()
        if connection is None:
            return Response({"store": ["Elegí una de tus tiendas Empretienda."]}, status=status.HTTP_400_BAD_REQUEST)

        upload = request.FILES.get("file")
        if upload is None:
            return Response({"file": ["Subí la planilla que exportaste de Empretienda."]}, status=status.HTTP_400_BAD_REQUEST)
        if upload.size > MAX_FILE_BYTES:
            return Response({"file": ["El archivo pesa más de 5 MB."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            headers, rows = parse_tabular_file(upload, upload.name)
        except ImportFileError as exc:
            return Response({"file": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)
        if len(rows) > MAX_ROWS:
            return Response(
                {"file": [f"La planilla tiene {len(rows)} filas: el máximo es {MAX_ROWS}. Exportá un rango de fechas más chico."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sent = request.data.get("mapping")
        if sent:
            try:
                mapping = spreadsheet.clean_mapping(json.loads(sent) if isinstance(sent, str) else sent, headers)
            except ValueError:
                return Response({"mapping": ["El mapeo de columnas no es válido."]}, status=status.HTTP_400_BAD_REQUEST)
        else:
            saved = (connection.preferences or {}).get(spreadsheet.MAPPING_PREF)
            mapping = spreadsheet.detect_mapping(headers, saved)
        mapping = {field: mapping.get(field) for field in spreadsheet.FIELD_LABELS}
        return connection, headers, rows, mapping

    @staticmethod
    def _fields():
        return [
            {"field": field, "label": label, "required": required}
            for field, label, required, _synonyms in spreadsheet.FIELDS
        ]


class EmpretiendaImportPreviewView(_SpreadsheetView):
    def post(self, request):
        loaded = self._load(request)
        if isinstance(loaded, Response):
            return loaded
        connection, headers, rows, mapping = loaded
        body = {"headers": headers, "fields": self._fields(), "mapping": mapping, "rows": len(rows)}
        missing = spreadsheet.missing_required(mapping)
        if missing:
            return Response(dict(body, missing=missing, orders=[]))

        groups = spreadsheet.group_orders(rows, mapping)
        existing = set(
            Order.objects.filter(store_connection=connection, external_id__in=[number for number, _ in groups]).values_list(
                "external_id", flat=True
            )
        )
        orders = []
        for number, group in groups:
            normalized, errors = spreadsheet.build_order(number, group, mapping)
            orders.append(
                {
                    "number": number,
                    "recipient": normalized.recipient_name,
                    "address": " ".join(part for part in (normalized.street, normalized.number) if part),
                    "city": normalized.city,
                    "state": normalized.state,
                    "postal_code": normalized.postal_code,
                    "items": len(normalized.items),
                    "status": normalized.status,
                    "result": "error" if errors else ("update" if number in existing else "new"),
                    "errors": errors,
                }
            )
        return Response(dict(body, missing=[], orders=orders))


class EmpretiendaImportConfirmView(_SpreadsheetView):
    def post(self, request):
        loaded = self._load(request)
        if isinstance(loaded, Response):
            return loaded
        connection, _headers, rows, mapping = loaded
        missing = spreadsheet.missing_required(mapping)
        if missing:
            return Response(
                {"detail": "Falta elegir la columna de: " + ", ".join(missing) + "."}, status=status.HTTP_400_BAD_REQUEST
            )

        counts = {"created": 0, "updated": 0, "unchanged": 0}
        failed, created_ids = [], []
        # Un solo aviso saliente con el resumen, no uno por pedido (como la
        # importación CSV/Excel genérica).
        with webhooks_suspended():
            for number, group in spreadsheet.group_orders(rows, mapping):
                normalized, errors = spreadsheet.build_order(number, group, mapping)
                if errors:
                    failed.append({"number": number, "errors": errors})
                    continue
                with transaction.atomic():
                    order, result = upsert_store_order(connection, normalized)
                counts[result] += 1
                if result == "created":
                    created_ids.append(order.pk)

        preferences = dict(connection.preferences or {}, **{spreadsheet.MAPPING_PREF: {k: v for k, v in mapping.items() if v}})
        StoreConnection.objects.filter(pk=connection.pk).update(preferences=preferences)
        if created_ids:
            dispatch_event(
                request.user,
                "orders.imported",
                {"count": len(created_ids), "source": Order.Source.STORE, "order_ids": created_ids},
            )
        record(
            request,
            category="orders",
            action="order.import",
            target=connection,
            target_type="storeconnection",
            target_repr=str(connection),
            changes={
                "empretienda_creados": {"from": None, "to": counts["created"]},
                "empretienda_actualizados": {"from": None, "to": counts["updated"]},
                "empretienda_con_error": {"from": None, "to": len(failed)},
            },
        )
        return Response(dict(counts, failed=failed))
