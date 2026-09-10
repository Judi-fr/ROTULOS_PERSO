"""Importación de pedidos desde CSV/Excel (story 21), en tres pasos —cada
uno su endpoint, ver el docstring de ``models.OrderImport``— más las
plantillas de mapeo guardadas y la plantilla descargable (story 22).
"""

from __future__ import annotations

import csv
import io

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record
from apps.integrations.webhooks import dispatch_event, webhooks_suspended

from .import_parsing import ImportFileError, parse_tabular_file
from .ingestion import TARGET_FIELDS, apply_mapping, auto_detect_mapping, create_order_from_data, validate_mapped_row
from .models import ImportMapping, Order, OrderImport
from .serializers import ImportMappingSerializer, OrderImportSerializer

PREVIEW_ROWS = 10


def _max_file_size_bytes():
    return getattr(settings, "ORDERS_IMPORT_MAX_FILE_SIZE_MB", 10) * 1024 * 1024


def _max_rows():
    return getattr(settings, "ORDERS_IMPORT_MAX_ROWS", 1000)


def _reread_import_file(order_import):
    """Reabre el archivo YA guardado y lo vuelve a parsear entero: tanto
    mapear como confirmar necesitan todas las filas, no solo la vista
    previa que guardó el upload."""
    order_import.file.open("rb")
    try:
        return parse_tabular_file(order_import.file, order_import.original_filename)
    except ImportFileError as exc:
        raise ValidationError({"file": str(exc)})
    finally:
        order_import.file.close()


class OrderImportUploadView(APIView):
    """POST /api/v1/orders/imports/  (multipart, campo ``file``)

    Paso 1: guarda el archivo, detecta delimitador/codificación (CSV) o
    lee la primera hoja (Excel), y devuelve encabezados + primeras
    ``PREVIEW_ROWS`` filas + un mapeo sugerido por auto-detección — el
    usuario ajusta ese mapeo en el paso 2, nunca hace falta escribirlo de
    cero si los encabezados son razonables.
    """

    parser_classes = [MultiPartParser]

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("orders.import")]

    def post(self, request):
        file_obj = request.FILES.get("file")
        if not file_obj:
            raise ValidationError({"file": "Es obligatorio."})

        if file_obj.size > _max_file_size_bytes():
            raise ValidationError(
                {
                    "file": (
                        f"El archivo supera el tamaño máximo permitido "
                        f"({_max_file_size_bytes() // (1024 * 1024)} MB)."
                    )
                }
            )

        try:
            headers, rows = parse_tabular_file(file_obj, file_obj.name)
        except ImportFileError as exc:
            raise ValidationError({"file": str(exc)})

        max_rows = _max_rows()
        if len(rows) > max_rows:
            raise ValidationError(
                {"file": f"El archivo supera el máximo de {max_rows} filas (tiene {len(rows)})."}
            )

        # parse_tabular_file ya consumió el stream (read()/openpyxl): hay
        # que rebobinarlo antes de que el FileField lo guarde, si no se
        # persiste un archivo vacío.
        file_obj.seek(0)

        order_import = OrderImport.objects.create(
            user=request.user,
            file=file_obj,
            original_filename=file_obj.name,
            status=OrderImport.Status.PENDING,
            total_rows=len(rows),
        )

        return Response(
            {
                "id": order_import.pk,
                "headers": headers,
                "preview_rows": rows[:PREVIEW_ROWS],
                "total_rows": len(rows),
                "suggested_mapping": auto_detect_mapping(headers),
                "target_fields": TARGET_FIELDS,
            },
            status=status.HTTP_201_CREATED,
        )


class OrderImportValidateView(APIView):
    """POST /api/v1/orders/imports/<id>/validate/  ``{"mapping": {...}}``

    Paso 2: aplica el mapeo a TODAS las filas y valida sin escribir nada
    en la base — devuelve cuántas están bien y el detalle de las que
    fallan, con número de fila (1-based contando el encabezado, así
    coincide con lo que se ve al abrir el archivo en una planilla).
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("orders.import")]

    def post(self, request, pk):
        order_import = get_object_or_404(OrderImport, pk=pk, user=request.user)
        mapping = request.data.get("mapping")
        if not isinstance(mapping, dict) or not mapping:
            raise ValidationError({"mapping": "Es obligatorio y no puede estar vacío."})

        headers, rows = _reread_import_file(order_import)

        valid_count = 0
        errors = []
        seen_external_ids = set()
        for row_number, row in enumerate(rows, start=2):
            data = apply_mapping(row, mapping)
            row_errors = validate_mapped_row(data)
            external_id = (data.get("external_id") or "").strip()
            if external_id:
                if external_id in seen_external_ids:
                    row_errors.append(f"external_id {external_id!r} repetido en el archivo.")
                seen_external_ids.add(external_id)
            if row_errors:
                errors.append({"fila": row_number, "columna": None, "mensaje": " ".join(row_errors)})
            else:
                valid_count += 1

        order_import.mapping = mapping
        order_import.status = OrderImport.Status.MAPPING
        order_import.save(update_fields=["mapping", "status", "updated_at"])

        return Response(
            {
                "id": order_import.pk,
                "total_rows": len(rows),
                "valid_count": valid_count,
                "error_count": len(errors),
                "errors": errors,
            }
        )


class OrderImportConfirmView(APIView):
    """POST /api/v1/orders/imports/<id>/confirm/  ``{"mapping": {...}}`` (opcional
    si ya se validó un mapeo en el paso 2)

    Paso 3: crea los pedidos, fila por fila, dentro de una transacción por
    fila (ver ``ingestion.create_order_from_data``). Una fila con error
    NUNCA aborta el resto: se saltea, se cuenta y se detalla — igual
    criterio que el lote de rótulos (``apps.labels.batch_views``). Un
    ``external_id`` ya existente para este usuario también se cuenta como
    salteado (no se duplica ni se pisa).

    Las altas del lote no disparan un webhook saliente por fila (con 1000
    filas serían 1000 llamadas HTTP encadenadas, ver
    ``apps.integrations.webhooks``): quedan silenciadas con
    ``webhooks_suspended()`` y, al terminar, se manda un único evento
    ``orders.imported`` con el resumen.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("orders.import")]

    def post(self, request, pk):
        order_import = get_object_or_404(OrderImport, pk=pk, user=request.user)
        mapping = request.data.get("mapping") or order_import.mapping
        if not mapping:
            raise ValidationError(
                {"mapping": "Todavía no se validó un mapeo para esta importación (ver el paso 2)."}
            )

        order_import.status = OrderImport.Status.PROCESSING
        order_import.mapping = mapping
        order_import.save(update_fields=["status", "mapping", "updated_at"])

        headers, rows = _reread_import_file(order_import)

        imported = 0
        skipped = 0
        errors = []
        created_order_ids = []
        with webhooks_suspended():
            for row_number, row in enumerate(rows, start=2):
                data = apply_mapping(row, mapping)
                row_errors = validate_mapped_row(data)
                if row_errors:
                    errors.append({"fila": row_number, "columna": None, "mensaje": " ".join(row_errors)})
                    continue
                try:
                    order, created = create_order_from_data(request.user, data, source=Order.Source.IMPORT)
                except Exception as exc:  # noqa: BLE001 - una fila rota no aborta el resto
                    errors.append({"fila": row_number, "columna": None, "mensaje": str(exc)})
                    continue
                if created:
                    imported += 1
                    created_order_ids.append(order.pk)
                else:
                    skipped += 1

        if created_order_ids:
            dispatch_event(
                request.user,
                "orders.imported",
                {"count": len(created_order_ids), "source": Order.Source.IMPORT, "order_ids": created_order_ids},
            )

        order_import.imported_count = imported
        order_import.skipped_count = skipped
        order_import.error_count = len(errors)
        order_import.errors = errors
        order_import.status = OrderImport.Status.DONE
        order_import.save()

        record(
            request,
            category="orders",
            action="order.import",
            target=order_import,
            target_type="orderimport",
            target_repr=str(order_import),
            changes={
                "imported": {"from": None, "to": imported},
                "skipped": {"from": None, "to": skipped},
                "errors": {"from": None, "to": len(errors)},
            },
        )

        return Response(OrderImportSerializer(order_import).data)


class ImportMappingViewSet(viewsets.ModelViewSet):
    """CRUD de las plantillas de mapeo propias del usuario (story 22):
    "la próxima vez que llegue el mismo formato de archivo se aplica de
    una"."""

    serializer_class = ImportMappingSerializer

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("orders.import_mappings")]

    def get_queryset(self):
        return ImportMapping.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class OrderImportTemplateView(APIView):
    """GET /api/v1/orders/imports/template/

    CSV de ejemplo con los encabezados correctos y una fila de muestra,
    para el cliente que prefiere adaptar SU planilla al formato del
    sistema en vez de mapear columnas."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("orders.import")]

    def get(self, request):
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow([meta["label"] for meta in TARGET_FIELDS.values()])
        writer.writerow(
            [
                "Juan Pérez",
                "Av. Siempre Viva",
                "742",
                "CABA",
                "Buenos Aires",
                "1000",
                "Timbre azul",
                "Paquete chico",
                "PED-0001",
            ]
        )
        response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="plantilla-importacion-pedidos.csv"'
        return response
