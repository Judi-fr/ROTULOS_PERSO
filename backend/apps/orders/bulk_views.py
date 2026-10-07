"""Acciones masivas sobre los pedidos propios.

- ``POST /orders/bulk-status/``: cambiar el estado de varios pedidos.
- ``GET  /orders/export/``: exportarlos a CSV o Excel.
- ``POST /orders/manifest/``: planilla de retiro (PDF) para el transportista.
- ``POST /orders/tracking-import/preview/`` + ``.../confirm/``: cargar los
  seguimientos desde la planilla que devuelve el transportista.

Igual que ``OrderViewSet``, todo se recorta a ``request.user``: un id de un
pedido ajeno se informa como "no encontrado", nunca se toca. Cada pedido se
procesa por separado y uno que no se puede actualizar (ya entregado,
cancelado) no corta el resto: la respuesta dice qué pasó con cada uno.
Ninguna de estas acciones tiene un permiso propio — usan los mismos que la
acción de a uno (los tests de accounts fijan el conjunto por rol).
"""

import json

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions_map import user_has_permission
from apps.accounts.role_permissions import HasRolePermission

from .bulk import (
    MAX_BULK_ORDERS,
    TRACKING_FIELDS,
    detect_columns,
    export_csv,
    export_xlsx,
    match_tracking_rows,
    order_number,
)
from .import_parsing import ImportFileError, parse_tabular_file
from .manifest import render_manifest_pdf
from .models import Order
from .serializers import OrderShipSerializer
from .shipping import apply_cancel, apply_shipping
from .views import filter_orders

# Una planilla de transportista de un día entra holgada; más que esto es
# casi seguro el archivo equivocado (un histórico entero).
MAX_TRACKING_ROWS = 2000
MAX_TRACKING_FILE_BYTES = 5 * 1024 * 1024

SHIPPING_TARGETS = [
    Order.Status.DISPATCHED,
    Order.Status.IN_TRANSIT,
    Order.Status.DELIVERED,
]


class OrderIdsSerializer(serializers.Serializer):
    order_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
        max_length=MAX_BULK_ORDERS,
        error_messages={
            "empty": "Elegí al menos un pedido.",
            "max_length": f"Se pueden procesar hasta {MAX_BULK_ORDERS} pedidos por vez.",
        },
    )


class BulkStatusSerializer(OrderIdsSerializer):
    status = serializers.ChoiceField(
        choices=[(s, s.label) for s in SHIPPING_TARGETS] + [(Order.Status.CANCELLED, "Cancelar")]
    )
    carrier = serializers.CharField(max_length=100, required=False, allow_blank=True)


class ManifestSerializer(OrderIdsSerializer):
    carrier = serializers.CharField(max_length=100, required=False, allow_blank=True)


class TrackingRowSerializer(serializers.Serializer):
    order_id = serializers.IntegerField(min_value=1)
    tracking_number = serializers.CharField(max_length=100)
    carrier = serializers.CharField(max_length=100, required=False, allow_blank=True)
    tracking_url = serializers.CharField(max_length=200, required=False, allow_blank=True)


class TrackingConfirmSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[(s, s.label) for s in SHIPPING_TARGETS])
    rows = TrackingRowSerializer(
        many=True,
        allow_empty=False,
        max_length=MAX_BULK_ORDERS,
        error_messages={"empty": "No hay filas para confirmar."},
    )


def _own_orders(user, ids):
    """``{id: pedido}`` de los pedidos propios entre ``ids``."""
    queryset = Order.objects.filter(user=user, pk__in=ids).select_related(
        "address", "store_connection", "user"
    )
    return {order.pk: order for order in queryset}


def _first_error(errors):
    """El primer mensaje legible de los errores de un serializer."""
    if isinstance(errors, dict):
        for value in errors.values():
            return _first_error(value)
    if isinstance(errors, list) and errors:
        return _first_error(errors[0])
    return str(errors)


def _result(order_id, ok, detail="", order=None):
    return {
        "order_id": order_id,
        "number": order_number(order) if order else str(order_id),
        "ok": ok,
        "detail": detail,
    }


def _ship_one(request, order, data):
    serializer = OrderShipSerializer(data=data, context={"order": order})
    if not serializer.is_valid():
        return False, _first_error(serializer.errors)
    apply_shipping(request, order, serializer.validated_data)
    return True, ""


def _summary(results):
    updated = sum(1 for item in results if item["ok"])
    return {"updated": updated, "failed": len(results) - updated, "results": results}


class BulkStatusView(APIView):
    """POST /api/v1/orders/bulk-status/

    ``{"order_ids": [..], "status": "dispatched"|"in_transit"|"delivered"|
    "cancelled", "carrier"?}``. Mismas reglas que de a uno: el estado de
    envío solo avanza y cancelar solo se puede antes del despacho. Cancelar
    pide ``orders.cancel``; lo demás, ``orders.create`` (como ``ship``)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = BulkStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        target = data["status"]

        permission = "orders.cancel" if target == Order.Status.CANCELLED else "orders.create"
        if not user_has_permission(request.user, permission):
            raise PermissionDenied("No tenés permiso para hacer esta acción.")

        ids = list(dict.fromkeys(data["order_ids"]))
        orders = _own_orders(request.user, ids)
        results = []
        for order_id in ids:
            order = orders.get(order_id)
            if order is None:
                results.append(_result(order_id, False, "No se encontró el pedido."))
                continue
            if target == Order.Status.CANCELLED:
                if not order.is_cancellable:
                    results.append(
                        _result(
                            order_id,
                            False,
                            f"Está «{order.get_status_display()}»: ya no se puede cancelar.",
                            order,
                        )
                    )
                    continue
                apply_cancel(request, order)
                results.append(_result(order_id, True, order=order))
                continue

            payload = {"status": target}
            if data.get("carrier"):
                payload["carrier"] = data["carrier"]
            ok, detail = _ship_one(request, order, payload)
            results.append(_result(order_id, ok, detail, order))
        return Response(_summary(results))


class OrderExportView(APIView):
    """GET /api/v1/orders/export/?file_type=csv|xlsx&<filtros del listado>

    Los mismos filtros que ``GET /orders/`` (``store``, ``status``,
    ``date_from``/``date_to``) o ``ids=1,2,3``. El parámetro se llama
    ``file_type`` y no ``format`` porque DRF reserva ``?format=`` para
    elegir el renderer y respondería 404 a "csv"."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("orders.view")]

    def get(self, request):
        params = request.query_params
        file_type = (params.get("file_type") or "csv").lower()
        if file_type not in ("csv", "xlsx"):
            raise ValidationError({"file_type": "Debe ser 'csv' o 'xlsx'."})

        queryset = filter_orders(
            Order.objects.filter(user=request.user).select_related("address", "store_connection", "user"),
            params,
        )
        raw_ids = (params.get("ids") or "").strip()
        if raw_ids:
            try:
                ids = [int(part) for part in raw_ids.split(",") if part.strip()]
            except ValueError:
                raise ValidationError({"ids": "Todos los IDs deben ser números enteros."})
            queryset = queryset.filter(pk__in=ids)
        orders = list(queryset.order_by("created_at", "pk"))

        today = timezone.localdate().strftime("%Y-%m-%d")
        if file_type == "xlsx":
            response = HttpResponse(
                export_xlsx(orders),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            response = HttpResponse(export_csv(orders), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="pedidos-{today}.{file_type}"'
        response["X-Order-Count"] = str(len(orders))
        return response


class DispatchManifestView(APIView):
    """POST /api/v1/orders/manifest/  ``{"order_ids": [..], "carrier"?}``

    Devuelve el PDF de la planilla de retiro. Un id ajeno o inexistente se
    ignora (no se puede listar un pedido de otro); si no queda ninguno, 400.
    No cambia el estado de nada: despachar es otra acción."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("orders.view")]

    def post(self, request):
        serializer = ManifestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        orders = sorted(
            _own_orders(request.user, data["order_ids"]).values(),
            key=lambda order: (order.created_at, order.pk),
        )
        if not orders:
            raise ValidationError({"order_ids": "No se encontró ninguno de los pedidos elegidos."})

        pdf = render_manifest_pdf(orders, request.user, carrier=(data.get("carrier") or "").strip())
        today = timezone.localdate().strftime("%Y-%m-%d")
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="planilla-retiro-{today}.pdf"'
        return response


class TrackingImportPreviewView(APIView):
    """POST /api/v1/orders/tracking-import/preview/ (multipart)

    ``file`` (CSV o .xlsx), opcionales ``mapping`` (JSON ``{"order",
    "tracking_number", "carrier", "tracking_url"}`` → encabezado),
    ``carrier`` (el transportista de toda la planilla, si no trae columna) y
    ``store`` (id o ``manual``, para desempatar dos tiendas con el mismo
    número de pedido).

    No guarda nada: lee la planilla, detecta las columnas y cruza cada fila
    con un pedido propio. El comerciante revisa y confirma con
    ``.../confirm/``, mandando de vuelta solo las filas que quiere aplicar."""

    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("orders.create")]

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": "Subí la planilla del transportista."})
        name = (upload.name or "").lower()
        if not name.endswith((".csv", ".xlsx", ".txt")):
            raise ValidationError({"file": "El archivo tiene que ser CSV o Excel (.xlsx)."})
        if upload.size > MAX_TRACKING_FILE_BYTES:
            raise ValidationError({"file": "El archivo es demasiado grande (máximo 5 MB)."})

        try:
            headers, rows = parse_tabular_file(upload, upload.name)
        except ImportFileError as exc:
            raise ValidationError({"file": str(exc)})
        except Exception:
            raise ValidationError({"file": "No se pudo leer el archivo. ¿Es un CSV o un Excel válido?"})
        if not rows:
            raise ValidationError({"file": "La planilla no tiene filas con datos."})
        if len(rows) > MAX_TRACKING_ROWS:
            raise ValidationError(
                {"file": f"La planilla tiene {len(rows)} filas: el máximo es {MAX_TRACKING_ROWS}."}
            )

        mapping = detect_columns(headers)
        raw_mapping = request.data.get("mapping")
        if raw_mapping:
            try:
                chosen = json.loads(raw_mapping)
            except (TypeError, ValueError):
                raise ValidationError({"mapping": "Formato de columnas inválido."})
            if not isinstance(chosen, dict):
                raise ValidationError({"mapping": "Formato de columnas inválido."})
            for field in TRACKING_FIELDS:
                if field in chosen:
                    value = chosen[field] or None
                    if value is not None and value not in headers:
                        raise ValidationError({"mapping": f"La columna «{value}» no está en la planilla."})
                    mapping[field] = value

        store = (request.data.get("store") or "").strip() or None
        if store and store != "manual" and not store.isdigit():
            raise ValidationError({"store": "Debe ser el id de una tienda o 'manual'."})

        if not mapping["order"] or not mapping["tracking_number"]:
            # Sin esas dos columnas no hay nada que cruzar: se devuelven los
            # encabezados para que el comerciante las elija a mano.
            rows_result = []
        else:
            rows_result = match_tracking_rows(
                request.user,
                rows,
                mapping,
                default_carrier=(request.data.get("carrier") or "").strip()[:100],
                store=int(store) if store and store.isdigit() else store,
            )

        counts = {}
        for item in rows_result:
            counts[item["state"]] = counts.get(item["state"], 0) + 1
        return Response(
            {
                "headers": headers,
                "mapping": mapping,
                "total_rows": len(rows),
                "counts": counts,
                "rows": rows_result,
            }
        )


class TrackingImportConfirmView(APIView):
    """POST /api/v1/orders/tracking-import/confirm/

    ``{"status": "dispatched"|"in_transit"|"delivered", "rows": [{"order_id",
    "tracking_number", "carrier"?, "tracking_url"?}]}``.

    Carga el seguimiento y lleva el pedido a ``status``. Si un pedido ya
    estaba más adelante (p. ej. en tránsito y se confirma "despachado"),
    conserva su estado y solo se le actualiza el seguimiento: la planilla no
    hace retroceder a nadie. Si vino de una tienda, la tienda recibe el aviso
    (``Order.save``)."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("orders.create")]

    def post(self, request):
        serializer = TrackingConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        target = data["status"]

        rows = data["rows"]
        orders = _own_orders(request.user, [row["order_id"] for row in rows])
        results = []
        done = set()
        for row in rows:
            order_id = row["order_id"]
            order = orders.get(order_id)
            if order is None:
                results.append(_result(order_id, False, "No se encontró el pedido."))
                continue
            if order_id in done:
                results.append(_result(order_id, False, "El pedido venía repetido.", order))
                continue
            done.add(order_id)
            effective = (
                target if Order.status_rank(target) >= Order.status_rank(order.status) else order.status
            )
            payload = {"status": effective, "tracking_number": row["tracking_number"]}
            for field in ("carrier", "tracking_url"):
                if row.get(field):
                    payload[field] = row[field]
            ok, detail = _ship_one(request, order, payload)
            results.append(_result(order_id, ok, detail, order))
        return Response(_summary(results))
