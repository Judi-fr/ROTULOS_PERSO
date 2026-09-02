"""Form de contacto ("Ayuda/Soporte") del dashboard + bandeja de soporte del admin.

    POST /api/v1/auth/support/        -> crea un mensaje de soporte propio
    GET  /api/v1/auth/support/        -> lista SOLO los mensajes propios
    GET  /api/v1/support-messages/         -> (admin) lista todos los mensajes
    GET  /api/v1/support-messages/N/       -> (admin) detalle
    PATCH /api/v1/support-messages/N/      -> (admin) cambia status y/o response
    GET  /api/v1/support-messages/metrics/ -> (admin) métricas para el panel de reportes
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone
from rest_framework import generics, mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.services import record

from .models import SupportMessage
from .pagination import UserAdminPagination
from .role_permissions import HasRolePermission


class SupportMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportMessage
        fields = ["id", "subject", "message", "status", "response", "responded_at", "created_at"]
        read_only_fields = ["id", "status", "response", "responded_at", "created_at"]

    def validate_subject(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("El asunto no puede estar vacío.")
        return value

    def validate_message(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("El mensaje no puede estar vacío.")
        return value


class SupportMessageView(generics.ListCreateAPIView):
    """POST: crea un mensaje propio. GET: lista SOLO los mensajes propios
    (nunca por id de parámetro), para que el usuario vea la respuesta del
    admin."""

    serializer_class = SupportMessageSerializer

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("support.create")]

    def get_queryset(self):
        return SupportMessage.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        instance = serializer.save(user=self.request.user)
        record(
            self.request,
            category="support",
            action="support.create",
            target=instance,
            target_type="support_message",
            target_repr=f"{instance.user.email}: {instance.subject}",
        )


class AdminSupportMessageSerializer(serializers.ModelSerializer):
    """Lectura completa + edición de ``status``/``response`` (bandeja admin)."""

    user_email = serializers.EmailField(source="user.email", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    handled_by_email = serializers.SerializerMethodField()

    class Meta:
        model = SupportMessage
        fields = [
            "id",
            "user",
            "user_email",
            "subject",
            "message",
            "status",
            "status_label",
            "response",
            "responded_at",
            "handled_by",
            "handled_by_email",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "user",
            "user_email",
            "subject",
            "message",
            "status_label",
            "responded_at",
            "handled_by",
            "handled_by_email",
            "created_at",
            "updated_at",
        ]

    def get_handled_by_email(self, obj):
        return obj.handled_by.email if obj.handled_by else ""

    def validate_response(self, value):
        return (value or "").strip()


class AdminSupportMessagePagination(UserAdminPagination):
    page_size = 20


class AdminSupportMessageViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """Bandeja de soporte del admin: solo lectura + PATCH de status/response."""

    queryset = SupportMessage.objects.select_related("user", "handled_by").all()
    serializer_class = AdminSupportMessageSerializer
    pagination_class = AdminSupportMessagePagination
    http_method_names = ["get", "patch", "head", "options"]

    def get_permissions(self):
        permission = "support.manage" if self.action == "partial_update" else "support.view_all"
        return [IsAuthenticated(), HasRolePermission(permission)]

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
        queryset = super().get_queryset()
        params = self.request.query_params

        status_param = params.get("status", "").strip()
        if status_param:
            queryset = queryset.filter(status=status_param)

        search = params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(user__email__icontains=search)
                | Q(subject__icontains=search)
                | Q(message__icontains=search)
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
        if ordering not in {"created_at", "-created_at", "status", "-status"}:
            ordering = "-created_at"
        return queryset.order_by(ordering)

    def list(self, request, *args, **kwargs):
        """Además del listado paginado, un bloque de conteos por estado
        (tarjetas del panel) sobre el total, no sobre la página filtrada."""
        response = super().list(request, *args, **kwargs)
        response.data["counts"] = {
            "pending": SupportMessage.objects.filter(
                status=SupportMessage.Status.PENDING
            ).count(),
            "in_progress": SupportMessage.objects.filter(
                status=SupportMessage.Status.IN_PROGRESS
            ).count(),
            "resolved": SupportMessage.objects.filter(
                status=SupportMessage.Status.RESOLVED
            ).count(),
        }
        return response

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        allowed_fields = {"status", "response"}
        extra_fields = set(request.data.keys()) - allowed_fields
        if extra_fields:
            raise ValidationError(
                {
                    "detail": (
                        "Solo se pueden editar 'status' y 'response'. "
                        f"Campos no permitidos: {', '.join(sorted(extra_fields))}."
                    )
                }
            )

        previous_status = instance.status
        previous_response = instance.response
        status_explicitly_set = "status" in request.data

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()

        response_text = (updated.response or "").strip()
        extra_update_fields = []
        if response_text and response_text != previous_response:
            updated.responded_at = timezone.now()
            updated.handled_by = request.user
            extra_update_fields += ["responded_at", "handled_by"]
            if updated.status == SupportMessage.Status.PENDING and not status_explicitly_set:
                updated.status = SupportMessage.Status.RESOLVED
                extra_update_fields.append("status")

        if extra_update_fields:
            updated.save(update_fields=extra_update_fields + ["updated_at"])

        target_repr = f"{updated.user.email}: {updated.subject}"
        if updated.status != previous_status:
            record(
                request,
                category="support",
                action="support.status_change",
                target=updated,
                target_type="support_message",
                target_repr=target_repr,
                changes={"status": {"from": previous_status, "to": updated.status}},
            )
        if response_text and response_text != previous_response:
            record(
                request,
                category="support",
                action="support.reply",
                target=updated,
                target_type="support_message",
                target_repr=target_repr,
                changes={"response": {"from": previous_response, "to": response_text}},
            )

        return Response(self.get_serializer(updated).data)

    @action(detail=False, methods=["get"])
    def metrics(self, request, *args, **kwargs):
        """GET /api/v1/support-messages/metrics/?months=6

        Métricas de soporte para el panel admin (``support.view_all``, misma
        ventana ``months`` que ``users/metrics/``/``orders/metrics/``).
        """
        raw_months = request.query_params.get("months", "6").strip()
        try:
            months = int(raw_months)
        except ValueError:
            raise ValidationError({"months": f"'months' debe ser un número entero: {raw_months!r}."})
        months = max(1, min(months, 12))
        cutoff = (timezone.now() - timedelta(days=30 * months)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        base_qs = SupportMessage.objects.filter(created_at__gte=cutoff)

        support_by_month = [
            {"month": row["month"].strftime("%Y-%m"), "count": row["count"]}
            for row in base_qs.annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(count=Count("id"))
            .order_by("month")
            if row["month"] is not None
        ]

        status_counts = dict(
            base_qs.values_list("status").annotate(count=Count("id")).values_list("status", "count")
        )
        support_by_status = [
            {"status": key, "label": label, "count": status_counts.get(key, 0)}
            for key, label in SupportMessage.Status.choices
        ]

        responded_qs = base_qs.filter(responded_at__isnull=False).only("created_at", "responded_at")
        total_seconds, sample_size = 0.0, 0
        for message in responded_qs:
            total_seconds += (message.responded_at - message.created_at).total_seconds()
            sample_size += 1
        avg_response_time = {
            "average_hours": round(total_seconds / sample_size / 3600, 1) if sample_size else None,
            "sample_size": sample_size,
        }

        return Response({
            "support_by_month": support_by_month,
            "support_by_status": support_by_status,
            "avg_response_time": avg_response_time,
        })
