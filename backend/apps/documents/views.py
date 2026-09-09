"""Vistas de documentos.

Mismo criterio que ``apps.labels``/``apps.orders``: el listado propio
(``DocumentViewSet``) se recorta siempre a ``request.user`` y nunca confía
en un id que mande el cliente; el listado de TODOS los documentos
(``AdminDocumentListView``) es un endpoint aparte, de solo lectura, con su
propio permiso (``documents.view_all``).

No hay ``create``/``update`` acá: un ``Document`` lo crea y completa el
proceso que lo genera (hoy, ``apps.labels.batch_views.LabelBatchView``),
esta app solo lista/descarga/borra.
"""

from __future__ import annotations

import mimetypes
import os
from datetime import datetime

from django.db.models import Q
from django.http import FileResponse, Http404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.pagination import UserAdminPagination
from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record

from .models import Document
from .serializers import AdminDocumentSerializer, DocumentSerializer


class DocumentViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Documentos propios del usuario autenticado: listar, ver, descargar
    y (soft-)borrar. No hay alta/edición manual (ver docstring del módulo)."""

    serializer_class = DocumentSerializer

    def get_permissions(self):
        if self.action == "destroy":
            permission = "documents.delete"
        else:
            permission = "documents.view"
        return [IsAuthenticated(), HasRolePermission(permission)]

    def get_queryset(self):
        queryset = Document.objects.filter(user=self.request.user, is_active=True)
        params = self.request.query_params

        search = params.get("search", "").strip()
        if search:
            queryset = queryset.filter(name__icontains=search)

        kind = params.get("kind", "").strip()
        if kind:
            queryset = queryset.filter(kind=kind)

        status_param = params.get("status", "").strip()
        if status_param:
            queryset = queryset.filter(status=status_param)

        date_from = self._parse_date_param("date_from")
        date_to = self._parse_date_param("date_to")
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        return queryset

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

    def destroy(self, request, *args, **kwargs):
        document = self.get_object()
        document.is_active = False
        document.save(update_fields=["is_active", "updated_at"])
        record(
            request,
            category="documents",
            action="document.delete",
            target=document,
            target_type="document",
            target_repr=str(document),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        """GET /api/v1/documents/<id>/download/

        Un documento ``processing``/``failed`` no tiene un archivo válido
        para servir: responde 404 (nunca un archivo vacío/roto).
        """
        document = self.get_object()
        if document.status != Document.Status.READY or not document.file:
            raise Http404("Este documento todavía no tiene un archivo para descargar.")

        filename = os.path.basename(document.file.name)
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        response = FileResponse(
            document.file.open("rb"), content_type=content_type, as_attachment=True, filename=filename
        )
        return response


class AdminDocumentPagination(UserAdminPagination):
    page_size = 20


class AdminDocumentListView(ListAPIView):
    """GET /api/v1/documents/admin/

    Documentos de TODOS los usuarios para el panel admin
    (``documents.view_all``). Solo lectura: el queryset de
    ``DocumentViewSet`` (self-service) no cambia, mismo patrón que
    ``AdminLabelListView``/``AdminOrderListView``.
    """

    serializer_class = AdminDocumentSerializer
    pagination_class = AdminDocumentPagination

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("documents.view_all")]

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
        queryset = Document.objects.select_related("user").all()
        params = self.request.query_params

        search = params.get("search", "").strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(user__email__icontains=search))

        kind = params.get("kind", "").strip()
        if kind:
            queryset = queryset.filter(kind=kind)

        status_param = params.get("status", "").strip()
        if status_param:
            queryset = queryset.filter(status=status_param)

        user_param = params.get("user", "").strip()
        if user_param:
            if user_param.isdigit():
                queryset = queryset.filter(user_id=int(user_param))
            else:
                queryset = queryset.filter(user__email__icontains=user_param)

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

        return queryset.order_by("-created_at")
