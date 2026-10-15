"""Lectura del registro de auditoría. Solo lectura: no hay ningún endpoint
de escritura/borrado (ver ``models.AuditLog``, inmutable por diseño)."""

from __future__ import annotations

import json
from datetime import timedelta

from django.db.models import Count, Q
from django.http import HttpResponse
from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.pagination import UserAdminPagination
from apps.accounts.role_permissions import HasRolePermission
from apps.common.date_filters import date_range_q
from apps.common.spreadsheet import csv_bytes, xlsx_bytes

from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditLogPagination(UserAdminPagination):
    page_size = 20


class AuditLogListView(ListAPIView):
    """GET /api/v1/audit/logs/

    Filtros combinables con AND: ``search`` (actor_email/target_repr/action),
    ``category``, ``action``, ``actor`` (id o email), ``target_type``,
    ``date_from``/``date_to`` (sobre ``created_at``), ``ordering``
    (``-created_at`` por defecto).
    """

    serializer_class = AuditLogSerializer
    pagination_class = AuditLogPagination

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("audit.view")]

    def get_queryset(self):
        return filter_logs(self.request.query_params)


def filter_logs(params):
    """Los filtros de ``GET /audit/logs/``, compartidos con la exportación:
    lo que se descarga es exactamente lo que se está viendo."""
    queryset = AuditLog.objects.select_related("actor").all()

    category = params.get("category", "").strip()
    if category:
        queryset = queryset.filter(category=category)

    action = params.get("action", "").strip()
    if action:
        queryset = queryset.filter(action=action)

    target_type = params.get("target_type", "").strip()
    if target_type:
        queryset = queryset.filter(target_type=target_type)

    actor = params.get("actor", "").strip()
    if actor:
        if actor.isdigit():
            queryset = queryset.filter(actor_id=int(actor))
        else:
            queryset = queryset.filter(actor_email__icontains=actor)

    search = params.get("search", "").strip()
    if search:
        queryset = queryset.filter(
            Q(actor_email__icontains=search)
            | Q(target_repr__icontains=search)
            | Q(action__icontains=search)
        )

    queryset = queryset.filter(date_range_q(params))

    ordering = params.get("ordering", "-created_at").strip() or "-created_at"
    if ordering not in {"created_at", "-created_at"}:
        ordering = "-created_at"
    return queryset.order_by(ordering)


class AuditActionsView(APIView):
    """GET /api/v1/audit/actions/

    Catálogo de categorías y acciones para poblar los selects del frontend
    sin hardcodearlos ahí.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("audit.view")]

    def get(self, request):
        categories = [
            {"key": key, "label": label} for key, label in AuditLog.Category.choices
        ]
        actions = [
            {
                "key": key,
                "label": label,
                "category": AuditLog.ACTION_CATEGORIES.get(key, ""),
            }
            for key, label in AuditLog.Action.choices
        ]
        return Response({"categories": categories, "actions": actions})


class AuditMetricsView(APIView):
    """GET /api/v1/audit/metrics/?months=6

    Métricas de actividad para el panel admin (``audit.view``, misma ventana
    ``months`` que ``users/metrics/``/``orders/metrics/``/
    ``support-messages/metrics/``): acciones por administrador/tipo de
    acción y logins exitosos vs. fallidos por mes.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("audit.view")]

    def get(self, request):
        raw_months = request.query_params.get("months", "6").strip()
        try:
            months = int(raw_months)
        except ValueError:
            raise ValidationError({"months": f"'months' debe ser un número entero: {raw_months!r}."})
        months = max(1, min(months, 12))
        cutoff = (timezone.now() - timedelta(days=30 * months)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        base_qs = AuditLog.objects.filter(created_at__gte=cutoff)

        by_actor = (
            base_qs.exclude(actor_email="")
            .values("actor_email")
            .annotate(count=Count("id"))
            .order_by("-count")[:10]
        )
        by_action = (
            base_qs.values("action").annotate(count=Count("id")).order_by("-count")
        )
        admin_activity = {
            "by_actor": [
                {"actor_email": row["actor_email"], "count": row["count"]} for row in by_actor
            ],
            "by_action": [
                {"action": row["action"], "count": row["count"]} for row in by_action
            ],
        }

        login_qs = (
            base_qs.filter(
                action__in=[AuditLog.Action.AUTH_LOGIN_SUCCESS, AuditLog.Action.AUTH_LOGIN_FAILED]
            )
            .annotate(month=TruncMonth("created_at"))
            .values("month", "action")
            .annotate(count=Count("id"))
            .order_by("month")
        )
        login_by_month = {}
        for row in login_qs:
            if row["month"] is None:
                continue
            key = row["month"].strftime("%Y-%m")
            bucket = login_by_month.setdefault(key, {"month": key, "success": 0, "failed": 0})
            if row["action"] == AuditLog.Action.AUTH_LOGIN_SUCCESS:
                bucket["success"] = row["count"]
            else:
                bucket["failed"] = row["count"]
        login_events = sorted(login_by_month.values(), key=lambda item: item["month"])

        return Response({"admin_activity": admin_activity, "login_events": login_events})


# Tope de filas por descarga: el registro crece sin límite y una planilla de
# cientos de miles de filas ni Excel la abre cómoda. Si se pasa, el archivo
# trae las más recientes según el orden pedido y ``X-Audit-Truncated: 1``.
AUDIT_EXPORT_MAX_ROWS = 10000

AUDIT_EXPORT_COLUMNS = [
    "Fecha",
    "Usuario",
    "Categoría",
    "Acción",
    "Tipo de objeto",
    "ID del objeto",
    "Objeto",
    "Cambios",
    "IP",
    "Navegador",
]


def _changes_text(changes):
    """``{"campo": {"from", "to"}}`` como ``campo: antes → después``, una
    entrada por línea; cualquier otra forma sale como JSON tal cual."""
    if not changes:
        return ""
    if not isinstance(changes, dict):
        return json.dumps(changes, ensure_ascii=False)

    def show(value):
        if value is None or value == "":
            return "—"
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    lines = []
    for field, diff in changes.items():
        if isinstance(diff, dict) and ("from" in diff or "to" in diff):
            lines.append(f"{field}: {show(diff.get('from'))} → {show(diff.get('to'))}")
        else:
            lines.append(f"{field}: {show(diff)}")
    return "\n".join(lines)


def audit_export_row(log):
    return [
        timezone.localtime(log.created_at).strftime("%Y-%m-%d %H:%M:%S"),
        log.actor_email or "Sistema",
        log.get_category_display(),
        log.get_action_display(),
        log.target_type,
        log.target_id,
        log.target_repr,
        _changes_text(log.changes),
        log.ip_address or "",
        log.user_agent,
    ]


class AuditLogExportView(APIView):
    """GET /api/v1/audit/logs/export/?file_type=csv|xlsx&<filtros del listado>

    Descarga lo que muestra ``GET /audit/logs/`` con los mismos filtros, sin
    paginar (hasta ``AUDIT_EXPORT_MAX_ROWS``). ``file_type`` y no ``format``:
    DRF reserva ``?format=`` para el renderer. Cantidad en ``X-Audit-Count``.
    Mismo permiso que ver el registro (``audit.view``)."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("audit.view")]

    def get(self, request):
        params = request.query_params
        file_type = (params.get("file_type") or "csv").lower()
        if file_type not in ("csv", "xlsx"):
            raise ValidationError({"file_type": "Debe ser 'csv' o 'xlsx'."})

        logs = list(filter_logs(params)[: AUDIT_EXPORT_MAX_ROWS + 1])
        truncated = len(logs) > AUDIT_EXPORT_MAX_ROWS
        logs = logs[:AUDIT_EXPORT_MAX_ROWS]
        rows = (audit_export_row(log) for log in logs)

        today = timezone.localdate().strftime("%Y-%m-%d")
        if file_type == "xlsx":
            response = HttpResponse(
                xlsx_bytes(AUDIT_EXPORT_COLUMNS, rows, "Auditoría"),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            response = HttpResponse(csv_bytes(AUDIT_EXPORT_COLUMNS, rows), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="auditoria-{today}.{file_type}"'
        response["X-Audit-Count"] = str(len(logs))
        response["X-Audit-Truncated"] = "1" if truncated else "0"
        return response
