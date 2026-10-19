from django.urls import path

from .views import MonitoringStatusView, MonitoringTestMailView

urlpatterns = [
    path("status/", MonitoringStatusView.as_view(), name="monitoring-status"),
    path("test-mail/", MonitoringTestMailView.as_view(), name="monitoring-test-mail"),
]
