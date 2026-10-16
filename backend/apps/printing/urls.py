from django.urls import path
from rest_framework.routers import SimpleRouter

from .agent_views import AgentJobFileView, AgentJobResultView, AgentNextJobView
from .views import AgentDownloadView, PrintAgentViewSet, PrinterViewSet, PrintJobViewSet

router = SimpleRouter()
router.register(r"agents", PrintAgentViewSet, basename="print-agent")
router.register(r"printers", PrinterViewSet, basename="printer")
router.register(r"jobs", PrintJobViewSet, basename="print-job")

urlpatterns = [
    path("agent/download/", AgentDownloadView.as_view(), name="print-agent-download"),
    path("agent/jobs/next/", AgentNextJobView.as_view(), name="print-agent-next"),
    path("agent/jobs/<int:job_id>/file/", AgentJobFileView.as_view(), name="print-agent-file"),
    path("agent/jobs/<int:job_id>/result/", AgentJobResultView.as_view(), name="print-agent-result"),
] + router.urls
