"""Vistas de plantillas y rótulos.

Mismo criterio que ``apps.orders``: el CRUD propio (``LabelViewSet``) se
recorta siempre a ``request.user`` y nunca confía en un id que mande el
cliente; el listado de TODOS los rótulos (``AdminLabelListView``) es un
endpoint aparte, de solo lectura, con su propio permiso (``labels.view_all``).
"""

from __future__ import annotations

from datetime import datetime

from django.core.files.base import ContentFile
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.pagination import UserAdminPagination
from apps.accounts.permissions_map import user_has_permission
from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record
from apps.orders.models import Order

from .models import Label, LabelTemplate
from .rendering import (
    BARCODE_HEIGHT_RANGE_CM,
    BARCODE_WIDTH_RANGE_CM,
    DEFAULT_BARCODE_HEIGHT_CM,
    DEFAULT_BARCODE_WIDTH_CM,
    DEFAULT_QR_SIZE_CM,
    QR_SIZE_RANGE_CM,
    build_barcode_drawing,
    build_label_context,
    build_qr_drawing,
    render_code_svg,
    render_label_pdf,
)
from .serializers import AdminLabelSerializer, LabelSerializer, LabelTemplateSerializer


class LabelViewSet(viewsets.ModelViewSet):
    """CRUD de los rótulos propios del usuario autenticado.

    El borrado es soft-delete (``is_active=False``): un rótulo nunca se
    destruye de forma permanente, igual que el resto del sistema.
    """

    serializer_class = LabelSerializer

    def get_permissions(self):
        if self.action == "create":
            permission = "labels.create"
        elif self.action == "destroy":
            permission = "labels.delete"
        elif self.action in ("update", "partial_update"):
            permission = "labels.edit"
        elif self.action == "duplicate":
            # Duplicar no modifica el original: crea uno nuevo a partir de él.
            permission = "labels.create"
        else:
            permission = "labels.view"
        return [IsAuthenticated(), HasRolePermission(permission)]

    def get_queryset(self):
        queryset = Label.objects.filter(user=self.request.user, is_active=True).select_related(
            "template", "order"
        )
        # ?search= filtra por nombre o destinatario (frontend/rotulos.html).
        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(client__icontains=search))
        return queryset

    def perform_create(self, serializer):
        label = serializer.save(user=self.request.user)
        record(
            self.request,
            category="labels",
            action="label.create",
            target=label,
            target_type="label",
            target_repr=str(label),
        )

    def perform_update(self, serializer):
        label = serializer.save()
        record(
            self.request,
            category="labels",
            action="label.update",
            target=label,
            target_type="label",
            target_repr=str(label),
        )

    def destroy(self, request, *args, **kwargs):
        label = self.get_object()
        label.is_active = False
        label.save(update_fields=["is_active", "updated_at"])
        record(
            request,
            category="labels",
            action="label.delete",
            target=label,
            target_type="label",
            target_repr=str(label),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def duplicate(self, request, pk=None):
        """POST /api/v1/labels/labels/<id>/duplicate/

        Clona el rótulo propio (nueva fila independiente, ``name + " (copia)"``):
        la forma de "partir de uno anterior" sin perder el original.
        """
        original = self.get_object()
        copy = Label.objects.create(
            user=original.user,
            template=original.template,
            name=f"{original.name} (copia)",
            client=original.client,
            order=original.order,
            width_cm=original.width_cm,
            height_cm=original.height_cm,
            design=original.design,
        )
        if original.logo:
            copy.logo.save(
                original.logo.name.rsplit("/", 1)[-1],
                ContentFile(original.logo.read()),
                save=False,
            )
        if original.thumbnail:
            copy.thumbnail.save(
                original.thumbnail.name.rsplit("/", 1)[-1],
                ContentFile(original.thumbnail.read()),
                save=False,
            )
        copy.save()
        record(
            request,
            category="labels",
            action="label.create",
            target=copy,
            target_type="label",
            target_repr=str(copy),
            changes={"duplicated_from": {"from": None, "to": original.pk}},
        )
        return Response(
            self.get_serializer(copy).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        """GET /api/v1/labels/labels/<id>/pdf/

        Rótulo definitivo (el que va a imprenta), armado en el servidor con
        ``apps.labels.rendering`` en vez del PNG/PDF del navegador. Si el
        rótulo tiene ``order``, los marcadores ``{{clave}}`` del diseño se
        resuelven con los datos de ese envío.
        """
        label = self.get_object()
        context = build_label_context(order=label.order, label=label)
        pdf_bytes = render_label_pdf(
            design=label.design,
            width_cm=label.width_cm,
            height_cm=label.height_cm,
            context=context,
            logo_file=label.logo if label.logo else None,
        )
        record(
            request,
            category="labels",
            action="label.render",
            target=label,
            target_type="label",
            target_repr=str(label),
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="rotulo-{label.pk}.pdf"'
        return response


class LabelTemplateViewSet(viewsets.ModelViewSet):
    """Plantillas de rótulo: lectura para todos (públicas + propias);
    crear/editar/borrar exige ``labels.manage_templates``."""

    serializer_class = LabelTemplateSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        return [IsAuthenticated(), HasRolePermission("labels.manage_templates")]

    def get_queryset(self):
        user = self.request.user
        return LabelTemplate.objects.filter(Q(is_public=True) | Q(owner=user))

    def perform_create(self, serializer):
        template = serializer.save(owner=self.request.user)
        record(
            self.request,
            category="labels",
            action="template.create",
            target=template,
            target_type="labeltemplate",
            target_repr=str(template),
        )

    def perform_update(self, serializer):
        template = serializer.save()
        record(
            self.request,
            category="labels",
            action="template.update",
            target=template,
            target_type="labeltemplate",
            target_repr=str(template),
        )

    def perform_destroy(self, instance):
        record(
            self.request,
            category="labels",
            action="template.delete",
            target=instance,
            target_type="labeltemplate",
            target_repr=str(instance),
        )
        instance.delete()


class AdminLabelPagination(UserAdminPagination):
    page_size = 20


class AdminLabelListView(ListAPIView):
    """GET /api/v1/labels/admin/

    Rótulos de TODOS los usuarios para el panel admin (``labels.view_all``).
    Solo lectura: el queryset de ``LabelViewSet`` (self-service) no cambia,
    este es un endpoint aparte, igual criterio que ``AdminOrderListView``.
    """

    serializer_class = AdminLabelSerializer
    pagination_class = AdminLabelPagination

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("labels.view_all")]

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
        queryset = Label.objects.select_related("user", "template", "order").all()
        params = self.request.query_params

        search = params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(client__icontains=search)
                | Q(user__email__icontains=search)
            )

        user_param = params.get("user", "").strip()
        if user_param:
            if user_param.isdigit():
                queryset = queryset.filter(user_id=int(user_param))
            else:
                queryset = queryset.filter(user__email__icontains=user_param)

        template_param = params.get("template", "").strip()
        if template_param.isdigit():
            queryset = queryset.filter(template_id=int(template_param))

        is_active_param = params.get("is_active", "").strip().lower()
        if is_active_param in ("true", "1"):
            queryset = queryset.filter(is_active=True)
        elif is_active_param in ("false", "0"):
            queryset = queryset.filter(is_active=False)

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


class RenderLabelView(APIView):
    """POST /api/v1/labels/render/

    Render SIN persistir: arma el PDF con el diseño de una plantilla y los
    datos de un pedido concreto ``{"template_id": N, "order_id": M}``. Es
    la semilla del render por lote — simple y sin estado, no crea ningún
    ``Label``.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("labels.render")]

    def post(self, request):
        template_id = request.data.get("template_id")
        order_id = request.data.get("order_id")
        if not template_id or not order_id:
            raise ValidationError(
                {"detail": "'template_id' y 'order_id' son obligatorios."}
            )
        try:
            template_id = int(template_id)
            order_id = int(order_id)
        except (TypeError, ValueError):
            raise ValidationError(
                {"detail": "'template_id' y 'order_id' deben ser números enteros."}
            )

        template = get_object_or_404(
            LabelTemplate.objects.filter(Q(is_public=True) | Q(owner=request.user)),
            pk=template_id,
        )

        # El pedido debe ser del usuario, salvo que sea admin con
        # 'labels.view_all' (mismo criterio que el resto de la app: la
        # identidad siempre sale de request.user, nunca de un id del cliente).
        if user_has_permission(request.user, "labels.view_all"):
            order_qs = Order.objects.all()
        else:
            order_qs = Order.objects.filter(user=request.user)
        order = get_object_or_404(
            order_qs.select_related("address", "user"), pk=order_id
        )

        context = build_label_context(order=order)
        pdf_bytes = render_label_pdf(
            design=template.design,
            width_cm=template.width_cm,
            height_cm=template.height_cm,
            context=context,
        )
        record(
            request,
            category="labels",
            action="label.render",
            target=order,
            target_type="order",
            target_repr=str(order),
            changes={"template": {"from": None, "to": template.pk}},
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="rotulo-plantilla-{template.pk}-pedido-{order.pk}.pdf"'
        )
        return response


def _parse_float_query_param(params, name, default, value_range):
    raw = params.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValidationError({name: f"'{name}' debe ser un número."})
    minimum, maximum = value_range
    if not (minimum <= value <= maximum):
        raise ValidationError({name: f"'{name}' debe estar entre {minimum} y {maximum}."})
    return value


class BarcodeImageView(APIView):
    """GET /api/v1/labels/barcode/?type=qr|code128|ean13&data=...

    SVG de un código QR/de barras suelto, con los mismos
    ``apps.labels.rendering.build_qr_drawing``/``build_barcode_drawing``
    que arman el PDF del rótulo (``render_code_svg``, Python puro vía
    ``reportlab.graphics.renderSVG`` — nada de rasterizar a mano). Sirve
    para que el editor muestre el código real en la vista previa (en vez
    de un cartelito "QR") y para depurar sin tener que generar un PDF
    entero.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("labels.view")]

    def get(self, request):
        params = request.query_params
        code_type = (params.get("type") or "qr").strip().lower()
        data = params.get("data") or ""
        if not data:
            raise ValidationError({"data": "El parámetro 'data' es obligatorio."})

        if code_type == "qr":
            size = _parse_float_query_param(params, "size", DEFAULT_QR_SIZE_CM, QR_SIZE_RANGE_CM)
            drawing = build_qr_drawing(data, size)
        elif code_type in ("code128", "ean13"):
            width = _parse_float_query_param(
                params, "width", DEFAULT_BARCODE_WIDTH_CM, BARCODE_WIDTH_RANGE_CM
            )
            height = _parse_float_query_param(
                params, "height", DEFAULT_BARCODE_HEIGHT_CM, BARCODE_HEIGHT_RANGE_CM
            )
            show_text = (params.get("show_text", "true") or "").strip().lower() not in (
                "false",
                "0",
            )
            try:
                drawing = build_barcode_drawing(data, code_type, width, height, show_text=show_text)
            except ValueError as exc:
                raise ValidationError({"detail": str(exc)})
        else:
            raise ValidationError({"type": "Debe ser 'qr', 'code128' o 'ean13'."})

        if drawing is None:
            raise ValidationError({"data": "No se pudo generar el código con esos datos."})

        svg = render_code_svg(drawing)
        return HttpResponse(svg, content_type="image/svg+xml")
