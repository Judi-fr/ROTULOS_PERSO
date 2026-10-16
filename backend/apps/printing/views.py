"""API del comerciante: agentes, impresoras y cola (``labels.batch``, el mismo
permiso que generar el lote). Todo es propio: cada consulta filtra por
``request.user``."""

from pathlib import Path

from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record
from apps.documents.models import Document

from . import services
from .models import PrintAgent, Printer, PrintJob
from .serializers import PrintAgentSerializer, PrinterSerializer, PrintJobSerializer

AGENT_SCRIPT = Path(__file__).resolve().parent / "agent" / "rotulos_print_agent.py"
PERMISSION = "labels.batch"


class _OwnPrintingMixin:
    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(PERMISSION)]

    def _audit(self, target, verb, changes=None):
        record(
            self.request,
            category="labels",
            action="printer.update",
            target=target,
            target_type=target._meta.model_name,
            target_repr=f"{target} ({verb})",
            changes=changes,
        )


class PrintAgentViewSet(_OwnPrintingMixin, viewsets.ModelViewSet):
    """``/printing/agents/``. Crear devuelve el token UNA vez (``token``);
    borrar lo revoca (deja de poder pedir trabajos) y suelta sus impresoras."""

    serializer_class = PrintAgentSerializer
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        return PrintAgent.objects.filter(owner=self.request.user, is_active=True)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        agent, token = services.create_agent(request.user, serializer.validated_data["name"])
        self._audit(agent, "agente creado")
        data = self.get_serializer(agent).data
        data["token"] = token
        return Response(data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        self._audit(serializer.save(), "agente renombrado")

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        instance.printers.update(agent=None)
        self._audit(instance, "agente revocado")


class PrinterViewSet(_OwnPrintingMixin, viewsets.ModelViewSet):
    """``/printing/printers/``. Borrar la archiva: los trabajos viejos la siguen nombrando."""

    serializer_class = PrinterSerializer
    http_method_names = ["get", "post", "patch", "delete"]

    def get_queryset(self):
        return Printer.objects.filter(owner=self.request.user, is_active=True).select_related("agent")

    def perform_create(self, serializer):
        printer = serializer.save(owner=self.request.user)
        if printer.is_default or not self.get_queryset().exclude(pk=printer.pk).exists():
            Printer.objects.filter(pk=printer.pk).update(is_default=True)
            services.set_default_printer(printer)
        self._audit(printer, "impresora agregada")

    def perform_update(self, serializer):
        printer = serializer.save()
        if printer.is_default:
            services.set_default_printer(printer)
        self._audit(printer, "impresora editada")

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.is_default = False
        instance.save(update_fields=["is_active", "is_default", "updated_at"])
        instance.jobs.filter(status=PrintJob.Status.PENDING).update(
            status=PrintJob.Status.CANCELLED, error_message="Se quitó la impresora."
        )
        self._audit(instance, "impresora quitada")


class PrintJobViewSet(
    _OwnPrintingMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """``/printing/jobs/`` (``?status=pending,error``, ``?printer=<id>``).
    ``POST`` ``{document_id, printer_id}`` manda a imprimir un lote ya
    generado (o uno de ``documentos.html`` otra vez)."""

    serializer_class = PrintJobSerializer

    def get_queryset(self):
        queryset = PrintJob.objects.filter(owner=self.request.user).select_related("printer", "document")
        statuses = [value for value in self.request.query_params.get("status", "").split(",") if value]
        if statuses:
            queryset = queryset.filter(status__in=statuses)
        printer = self.request.query_params.get("printer")
        if printer:
            queryset = queryset.filter(printer_id=printer)
        return queryset

    def create(self, request, *args, **kwargs):
        printer = get_object_or_404(Printer, pk=request.data.get("printer_id"), owner=request.user, is_active=True)
        document = get_object_or_404(Document, pk=request.data.get("document_id"), user=request.user, is_active=True)
        try:
            job = services.create_job(request.user, printer, document)
        except services.PrintingError as error:
            raise ValidationError({"detail": str(error)}) from error
        return Response(self.get_serializer(job).data, status=status.HTTP_201_CREATED)

    def _transition(self, change):
        job = self.get_object()
        try:
            change(job)
        except services.PrintingError as error:
            raise ValidationError({"detail": str(error)}) from error
        return Response(self.get_serializer(job).data)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        return self._transition(services.retry_job)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return self._transition(services.cancel_job)


class AgentDownloadView(APIView):
    """``GET /printing/agent/download/`` — el programa del agente (un solo
    archivo de Python 3, sin dependencias)."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission(PERMISSION)]

    def get(self, request):
        return FileResponse(
            AGENT_SCRIPT.open("rb"),
            as_attachment=True,
            filename=AGENT_SCRIPT.name,
            content_type="text/x-python",
        )
