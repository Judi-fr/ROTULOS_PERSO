"""Vistas de plantillas y rótulos.

Mismo criterio que ``apps.orders``: el CRUD propio (``LabelViewSet``) se
recorta siempre a ``request.user`` y nunca confía en un id que mande el
cliente; el listado de TODOS los rótulos (``AdminLabelListView``) es un
endpoint aparte, de solo lectura, con su propio permiso (``labels.view_all``).
"""

from __future__ import annotations

from datetime import datetime

from django.core.files.base import ContentFile
from django.db.models import Prefetch, ProtectedError, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated, SAFE_METHODS
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.pagination import UserAdminPagination
from apps.accounts.permissions_map import user_has_permission
from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record
from apps.orders.models import Order

from .models import ElementoPlantilla, Label, LabelTemplate, Plantilla, VariableRotulo
from .rendering import (
    BARCODE_HEIGHT_RANGE_CM,
    BARCODE_WIDTH_RANGE_CM,
    DEFAULT_BARCODE_HEIGHT_CM,
    DEFAULT_BARCODE_WIDTH_CM,
    DEFAULT_QR_SIZE_CM,
    QR_SIZE_RANGE_CM,
    SAMPLE_LABEL_CONTEXT,
    build_barcode_drawing,
    build_computed_context,
    build_label_context,
    build_qr_drawing,
    render_code_svg,
    render_label_pdf,
)
from .render import renderizar_pdf, renderizar_png
from .render.fuentes import FuenteNoDisponible, familias_disponibles
from .serializers import (
    AdminLabelSerializer,
    LabelSerializer,
    LabelTemplateSerializer,
    PlantillaSerializer,
    RenderizarSerializer,
    VariableRotuloSerializer,
)


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
        computed = build_computed_context(owner=label.user)
        pdf_bytes = render_label_pdf(
            design=label.design,
            width_cm=label.width_cm,
            height_cm=label.height_cm,
            context=context,
            logo_file=label.logo if label.logo else None,
            computed=computed,
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
    """Plantillas de rótulo: lectura para todos (públicas + propias
    activas). Crear/editar una plantilla PROPIA privada exige
    ``labels.template_create`` (self-service, los cuatro roles); tocar una
    plantilla PÚBLICA o de OTRO usuario, o marcar una como pública, exige
    ``labels.manage_templates`` (solo admin) — el chequeo fino vive en
    ``perform_create``/``perform_update``/``destroy`` porque depende del
    objeto/los datos, no solo de la acción (``get_permissions`` solo
    filtra la entrada mínima)."""

    serializer_class = LabelTemplateSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve", "preview"):
            return [IsAuthenticated()]
        return [IsAuthenticated(), HasRolePermission("labels.template_create")]

    def get_queryset(self):
        user = self.request.user
        queryset = LabelTemplate.objects.filter(
            Q(is_public=True) | Q(owner=user), is_active=True
        )
        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(name__icontains=search)
        return queryset

    def _can_manage_templates(self):
        return user_has_permission(self.request.user, "labels.manage_templates")

    def perform_create(self, serializer):
        is_public = bool(serializer.validated_data.get("is_public", False))
        if is_public and not self._can_manage_templates():
            raise PermissionDenied("Solo un administrador puede crear una plantilla pública.")
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
        instance = serializer.instance
        user = self.request.user
        is_own_private = instance.owner_id == user.id and not instance.is_public
        if not self._can_manage_templates():
            if not is_own_private:
                raise PermissionDenied("Solo podés editar tus propias plantillas privadas.")
            requested_is_public = serializer.validated_data.get("is_public", instance.is_public)
            if requested_is_public:
                raise PermissionDenied(
                    "Solo un administrador puede marcar una plantilla como pública."
                )
        template = serializer.save()
        record(
            self.request,
            category="labels",
            action="template.update",
            target=template,
            target_type="labeltemplate",
            target_repr=str(template),
        )

    def destroy(self, request, *args, **kwargs):
        """Archivar (soft-delete), no borrar de verdad: mismo criterio que
        ``Label``/``Document`` — los rótulos ya generados con esta
        plantilla siguen apuntándola (``Label.template``)."""
        instance = self.get_object()
        is_own_private = instance.owner_id == request.user.id and not instance.is_public
        if not self._can_manage_templates() and not is_own_private:
            raise PermissionDenied("Solo podés archivar tus propias plantillas privadas.")
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])
        record(
            request,
            category="labels",
            action="template.delete",
            target=instance,
            target_type="labeltemplate",
            target_repr=str(instance),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def duplicate(self, request, pk=None):
        """POST /api/v1/labels/templates/<id>/duplicate/

        Copia PROPIA y privada (``" (copia)"``), sin importar si el
        original es público o de otro usuario: duplicar no modifica el
        original, así que alcanza con ``labels.template_create``.
        """
        original = self.get_object()
        copy = LabelTemplate.objects.create(
            name=f"{original.name} (copia)",
            description=original.description,
            owner=request.user,
            is_public=False,
            width_cm=original.width_cm,
            height_cm=original.height_cm,
            design=original.design,
        )
        if original.preview:
            copy.preview.save(
                original.preview.name.rsplit("/", 1)[-1],
                ContentFile(original.preview.read()),
                save=True,
            )
        record(
            request,
            category="labels",
            action="template.create",
            target=copy,
            target_type="labeltemplate",
            target_repr=str(copy),
            changes={"duplicated_from": {"from": None, "to": original.pk}},
        )
        return Response(
            self.get_serializer(copy).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get"])
    def preview(self, request, pk=None):
        """GET /api/v1/labels/templates/<id>/preview/

        PDF de la plantilla con datos de EJEMPLO (``rendering.
        SAMPLE_LABEL_CONTEXT``), no de un pedido real: la misma cadena de
        render que arma el rótulo definitivo (``render_label_pdf``), para
        que la vista previa de ``frontend/plantillas.html`` sea fiel al
        resultado real sin necesitar un pedido.
        """
        template = self.get_object()
        computed = build_computed_context(owner=request.user, is_preview=True)
        pdf_bytes = render_label_pdf(
            design=template.design,
            width_cm=template.width_cm,
            height_cm=template.height_cm,
            context=SAMPLE_LABEL_CONTEXT,
            computed=computed,
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="plantilla-{template.pk}-preview.pdf"'
        return response


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
        # /labels/render/ es una vista previa sin persistir (ver el
        # docstring de la clase): no consume la numeración secuencial real,
        # el flag es explícito acá, no se adivina del request.
        computed = build_computed_context(owner=request.user, is_preview=True)
        pdf_bytes = render_label_pdf(
            design=template.design,
            width_cm=template.width_cm,
            height_cm=template.height_cm,
            context=context,
            computed=computed,
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

# ---------------------------------------------------------------------------
# Plantillas por elementos / catálogo de variables (integradas desde
# backend_echu), adaptadas al sistema de roles/permisos del backend.
# ---------------------------------------------------------------------------


class FuentesView(APIView):
    """GET /api/v1/labels/fuentes/

    Familias tipográficas que el editor puede ofrecer. La disponibilidad
    depende de la máquina: el PDF sale siempre, pero el PNG necesita un
    ``.ttf`` instalado.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("plantillas.view")]

    def get(self, request):
        return Response(familias_disponibles())


class VariableRotuloViewSet(viewsets.ModelViewSet):
    """CRUD del catálogo de variables que puede contener un rótulo.

    Leer el catálogo lo puede hacer cualquier usuario autenticado (el editor
    lo necesita para armar sus selectores); modificarlo queda reservado a
    administradores (``variables.manage``), porque es un recurso compartido
    por todas las plantillas.
    """

    serializer_class = VariableRotuloSerializer
    pagination_class = None

    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [IsAuthenticated(), HasRolePermission("variables.view")]
        return [IsAuthenticated(), HasRolePermission("variables.manage")]

    def get_queryset(self):
        queryset = VariableRotulo.objects.select_related("creada_por")
        if self.action == "list":
            incluir = self.request.query_params.get("incluir_inactivas")
            if incluir not in ("1", "true", "True"):
                queryset = queryset.filter(activa=True)
        return queryset

    def perform_create(self, serializer):
        variable = serializer.save(creada_por=self.request.user)
        record(
            self.request,
            category="labels",
            action="variable_rotulo.create",
            target=variable,
            target_type="variablerotulo",
            target_repr=str(variable),
        )

    def perform_update(self, serializer):
        variable = serializer.save()
        record(
            self.request,
            category="labels",
            action="variable_rotulo.update",
            target=variable,
            target_type="variablerotulo",
            target_repr=str(variable),
        )

    def destroy(self, request, *args, **kwargs):
        """Elimina una variable, salvo que sea del sistema o esté en uso."""
        variable = self.get_object()

        if variable.es_sistema:
            return Response(
                {
                    "detail": "Las variables del sistema no se pueden eliminar. "
                    "Si no se usa, marcala como inactiva."
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Se captura antes de borrar: tras el delete(), la instancia pierde
        # su pk y ya no sirve como ``target`` de record().
        target_id = str(variable.pk)
        target_repr = str(variable)

        try:
            variable.delete()
        except ProtectedError:
            return Response(
                {
                    "detail": "La variable está en uso en alguna plantilla. "
                    "Marcala como inactiva en lugar de eliminarla."
                },
                status=status.HTTP_409_CONFLICT,
            )

        record(
            request,
            category="labels",
            action="variable_rotulo.delete",
            target_type="variablerotulo",
            target_id=target_id,
            target_repr=target_repr,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class PlantillaViewSet(viewsets.ModelViewSet):
    """CRUD de plantillas de rótulos por elementos.

    Self-service: cada usuario opera sobre SUS propias plantillas
    (``creada_por``). Leer/renderizar exige ``plantillas.view``; escribir
    exige ``plantillas.edit``.
    """

    serializer_class = PlantillaSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve", "renderizar"):
            return [IsAuthenticated(), HasRolePermission("plantillas.view")]
        return [IsAuthenticated(), HasRolePermission("plantillas.edit")]

    def get_queryset(self):
        return (
            Plantilla.objects.filter(creada_por=self.request.user)
            .select_related("creada_por")
            .prefetch_related(
                Prefetch(
                    "elementos",
                    queryset=ElementoPlantilla.objects.select_related("variable"),
                )
            )
        )

    def perform_create(self, serializer):
        plantilla = serializer.save(creada_por=self.request.user)
        record(
            self.request,
            category="labels",
            action="plantilla.create",
            target=plantilla,
            target_type="plantilla",
            target_repr=str(plantilla),
        )

    def perform_update(self, serializer):
        plantilla = serializer.save()
        record(
            self.request,
            category="labels",
            action="plantilla.update",
            target=plantilla,
            target_type="plantilla",
            target_repr=str(plantilla),
        )

    def perform_destroy(self, instance):
        # Se captura antes de borrar: tras el delete(), la instancia pierde
        # su pk y ya no sirve como ``target`` de record().
        target_id = str(instance.pk)
        target_repr = str(instance)
        instance.delete()
        record(
            self.request,
            category="labels",
            action="plantilla.delete",
            target_type="plantilla",
            target_id=target_id,
            target_repr=target_repr,
        )

    @action(detail=True, methods=["post"])
    def renderizar(self, request, pk=None):
        """POST /api/v1/labels/plantillas/<id>/renderizar/

        Convierte un diseño guardado en algo que se pega en un paquete:
        ``{"datos": {...}}`` un rótulo, ``{}`` vista previa, o
        ``{"lote": [{...}, ...]}`` un PDF de varias páginas.
        """
        plantilla = self.get_object()
        entrada = RenderizarSerializer(data=request.data)
        entrada.is_valid(raise_exception=True)
        opciones = entrada.validated_data

        try:
            if opciones["formato"] == "png":
                contenido, informe = renderizar_png(
                    plantilla,
                    datos=opciones.get("datos"),
                    usuario=request.user,
                    dpi=opciones.get("dpi"),
                )
                tipo, extension = "image/png", "png"
            else:
                contenido, informe = renderizar_pdf(
                    plantilla,
                    datos=opciones.get("datos"),
                    usuario=request.user,
                    lote=opciones.get("lote"),
                )
                tipo, extension = "application/pdf", "pdf"
        except FuenteNoDisponible as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)

        respuesta = HttpResponse(contenido, content_type=tipo)
        respuesta["Content-Disposition"] = (
            f'attachment; filename="rotulo-{plantilla.pk}.{extension}"'
        )
        if informe["faltantes"]:
            respuesta["X-Rotulo-Faltantes"] = ",".join(informe["faltantes"])
        if informe["truncados"]:
            respuesta["X-Rotulo-Truncados"] = ",".join(informe["truncados"])
        if informe["avisos"]:
            respuesta["X-Rotulo-Avisos"] = ",".join(informe["avisos"])
        return respuesta
