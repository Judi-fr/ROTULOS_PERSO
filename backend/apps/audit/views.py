"""Lectura del registro de auditoría. Solo lectura: no hay ningún endpoint
de escritura/borrado (ver ``models.AuditLog``, inmutable por diseño)."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.pagination import UserAdminPagination
from apps.accounts.role_permissions import HasRolePermission

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

    def _parse_date_param(self, param_name):
        raw = self.request.query_params.get(param_name, "").strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            raise ValidationError(
                {param_name: f"Formato de fecha inválido (usar YYYY-MM-DD): {raw!r}."}
            )

    def get_queryset(self):
        queryset = AuditLog.objects.select_related("actor").all()
        params = self.request.query_params

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

        date_from = self._parse_date_param("date_from")
        date_to = self._parse_date_param("date_to")
        if date_from and date_to and date_from > date_to:
            raise ValidationError(
                {"date_to": "'date_to' no puede ser anterior a 'date_from'."}
            )
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

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
