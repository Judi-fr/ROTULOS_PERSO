"""Rutas de auditoría. Se montan bajo ``/api/v1/audit/`` (ver config/urls.py)."""

from django.urls import path

from .views import AuditActionsView, AuditLogListView, AuditMetricsView

urlpatterns = [
    path("logs/", AuditLogListView.as_view(), name="audit-log-list"),
    path("actions/", AuditActionsView.as_view(), name="audit-actions"),
    path("metrics/", AuditMetricsView.as_view(), name="audit-metrics"),
]
