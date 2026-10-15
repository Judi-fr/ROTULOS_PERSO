"""Rutas de auditoría. Se montan bajo ``/api/v1/audit/`` (ver config/urls.py)."""

from django.urls import path

from .views import AuditActionsView, AuditLogExportView, AuditLogListView, AuditMetricsView

urlpatterns = [
    path("logs/", AuditLogListView.as_view(), name="audit-log-list"),
    path("logs/export/", AuditLogExportView.as_view(), name="audit-log-export"),
    path("actions/", AuditActionsView.as_view(), name="audit-actions"),
    path("metrics/", AuditMetricsView.as_view(), name="audit-metrics"),
]
