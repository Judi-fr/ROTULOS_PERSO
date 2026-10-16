"""API del agente de impresión (``agent/rotulos_print_agent.py``).

Sin sesión: se autentica con ``Authorization: Agent <token>`` (``PrintAgent``),
que solo ve los trabajos de las impresoras asignadas a él. Tres pasos:

1. ``GET  agent/jobs/next/`` — toma el próximo trabajo (200) o nada (204).
   Cada consulta cuenta como "el agente está en línea".
2. ``GET  agent/jobs/<id>/file/`` — el archivo (PDF o ZPL) de ese trabajo.
3. ``POST agent/jobs/<id>/result/`` ``{ok, error}`` — cómo terminó.
"""

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import services
from .models import PrintJob


class _AgentView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "print_agent"

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        header = request.META.get("HTTP_AUTHORIZATION", "")
        scheme, _, token = header.partition(" ")
        agent = services.agent_for_token(token.strip()) if scheme.lower() == "agent" else None
        if agent is None:
            raise AuthenticationFailed("Token de agente inválido o revocado: generá uno nuevo en Impresoras.")
        self.agent = agent
        services.touch_agent(agent, request.META.get("HTTP_X_AGENT_VERSION", ""))

    def get_authenticate_header(self, request):
        # Sin esto DRF contesta 403 (no hay authentication_classes de donde
        # sacar el esquema); un token malo es un 401 con su esquema.
        return "Agent"

    def get_job(self, job_id):
        return get_object_or_404(PrintJob.objects.select_related("printer", "document"), pk=job_id, agent=self.agent)


class AgentNextJobView(_AgentView):
    def get(self, request):
        job = services.claim_next_job(self.agent)
        if job is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        printer = job.printer
        return Response(
            {
                "id": job.pk,
                "format": printer.format,
                "label_count": job.label_count,
                "attempt": job.attempts,
                "printer": {
                    "id": printer.pk,
                    "name": printer.name,
                    "connection": printer.connection,
                    "host": printer.host,
                    "port": printer.port,
                    "queue": printer.queue,
                },
            }
        )


class AgentJobFileView(_AgentView):
    def get(self, request, job_id):
        job = self.get_job(job_id)
        if job.status != PrintJob.Status.PRINTING or job.document is None or not job.document.file:
            return Response({"detail": "Ese trabajo no está para imprimir."}, status=status.HTTP_409_CONFLICT)
        with job.document.file.open("rb") as handle:
            content = handle.read()
        content_type = "application/pdf" if job.printer and job.printer.format == "pdf" else "application/octet-stream"
        return HttpResponse(content, content_type=content_type)


class AgentJobResultView(_AgentView):
    def post(self, request, job_id):
        job = self.get_job(job_id)
        ok = request.data.get("ok")
        if not isinstance(ok, bool):
            raise ValidationError({"ok": "Mandá true o false."})
        try:
            services.finish_job(self.agent, job, ok=ok, error=request.data.get("error", ""))
        except services.PrintingError as error:
            return Response({"detail": str(error)}, status=status.HTTP_409_CONFLICT)
        return Response({"status": job.status})
