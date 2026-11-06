"""Vistas de plantillas y rótulos.

Mismo criterio que ``apps.orders``: todo se recorta siempre al comercio actual y
nunca confía en un id que mande el cliente.
"""

from __future__ import annotations

import re

from django.core.files.base import ContentFile
from django.db.models import Max, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions_map import user_has_permission
from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record
from apps.orders.models import Order
from apps.accounts.teams import NoTeam, data_owner, order_scope_q

from .models import LabelImage, LabelSequence, LabelTemplate
from .label_rendering import (
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
    build_preview_context,
    build_qr_drawing,
    render_code_svg,
    render_label_pdf,
    sender_logo,
)
from . import label_images
from .template_versions import record_version, restore_version
from .thumbnails import refresh_template_thumbnail
from .zpl import DEFAULT_DPMM, SUPPORTED_DPMM, render_label_zpl
from .serializers import (
    LabelImageSerializer,
    LabelTemplateSerializer,
    LabelTemplateVersionSerializer,
)

ID_RE = re.compile(r"[0-9]{1,18}")
MAX_IDS_PER_REQUEST = 200


class _AnyRolePermission(HasRolePermission):
    """Alcanza con uno de los permisos."""

    def __init__(self, *permission_keys):
        super().__init__(permission_keys[0])
        self.permission_keys = permission_keys

    def has_permission(self, request, view):
        for key in self.permission_keys:
            self.permission_key = key
            if super().has_permission(request, view):
                return True
        return False


class LabelImageViewSet(viewsets.ModelViewSet):
    """Biblioteca de imágenes del comercio actual: listar (activas, sin
    paginar), subir y ocultar. Mismos permisos que las plantillas."""

    serializer_class = LabelImageSerializer
    pagination_class = None
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_permissions(self):
        if self.action == "destroy":
            return [IsAuthenticated(), HasRolePermission("labels.template_delete")]
        if self.action == "list":
            # Quien edita rótulos guardados tiene que ver las imágenes que
            # sus diseños usan, aunque no pueda subir ni administrar plantillas.
            return [IsAuthenticated(), _AnyRolePermission("labels.template_create", "labels.edit")]
        return [IsAuthenticated(), HasRolePermission("labels.template_create")]

    def get_queryset(self):
        return LabelImage.objects.filter(owner=data_owner(self.request.user), is_active=True)

    def list(self, request, *args, **kwargs):
        """Sin ``?ids``, las imágenes activas del comercio. Con ``?ids=1,2``,
        esas filas aunque estén ocultas, siempre que el usuario pueda usarlas
        (``allowed_image_ids``): el editor dibuja lo que el PDF imprime. Con
        ``?template=<id>`` también valen las de ese diseño, si puede leerlo."""
        raw = request.query_params.get("ids")
        if raw is None:
            return super().list(request, *args, **kwargs)
        parts = [part.strip() for part in raw.split(",")]
        wanted = {int(part) for part in parts if ID_RE.fullmatch(part)}
        wanted = set(sorted(wanted)[:MAX_IDS_PER_REQUEST])
        extra = []
        template_id = request.query_params.get("template", "")
        if ID_RE.fullmatch(template_id.strip()):
            template = LabelTemplate.objects.filter(
                Q(is_public=True) | Q(owner=data_owner(request.user)), pk=int(template_id.strip()), is_active=True
            ).first()
            if template is not None:
                extra.append(template.design)
        rows = LabelImage.objects.filter(
            pk__in=wanted & label_images.allowed_image_ids(request.user, extra)
        ).order_by("pk")
        return Response(self.get_serializer(rows, many=True).data)

    def create(self, request, *args, **kwargs):
        owner = data_owner(request.user)
        if LabelImage.objects.filter(owner=owner, is_active=True).count() >= label_images.MAX_IMAGES_PER_OWNER:
            raise ValidationError(
                f"Llegaste al máximo de {label_images.MAX_IMAGES_PER_OWNER} imágenes. Borrá alguna para subir otra."
            )
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(owner=owner, created_by=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        # Se oculta, el archivo queda: lo que ya la usa sigue imprimiéndola.
        instance.is_active = False
        instance.save(update_fields=["is_active"])


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
        if self.action in ("list", "retrieve", "preview", "versions", "version_preview"):
            return [IsAuthenticated()]
        # Crear y duplicar / editar y restaurar / borrar: tres permisos desde
        # la migración 0029 (antes los tres eran "labels.template_create").
        if self.action in ("update", "partial_update", "restore_version"):
            return [IsAuthenticated(), HasRolePermission("labels.template_edit")]
        if self.action == "destroy":
            return [IsAuthenticated(), HasRolePermission("labels.template_delete")]
        if self.action == "list":
            # Quien edita rótulos guardados tiene que ver las imágenes que
            # sus diseños usan, aunque no pueda subir ni administrar plantillas.
            return [IsAuthenticated(), _AnyRolePermission("labels.template_create", "labels.edit")]
        return [IsAuthenticated(), HasRolePermission("labels.template_create")]

    def get_queryset(self):
        user = data_owner(self.request.user)
        queryset = LabelTemplate.objects.filter(
            Q(is_public=True) | Q(owner=user), is_active=True
        ).annotate(current_version=Max("versions__number"))
        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(name__icontains=search)
        # ?gallery=1: los diseños de "+ Nueva plantilla", en su orden.
        if self.request.query_params.get("gallery") == "1":
            queryset = queryset.filter(owner__isnull=True, is_public=True, gallery_order__isnull=False).order_by(
                "gallery_order"
            )
        return queryset

    def _can_manage_templates(self):
        return user_has_permission(self.request.user, "labels.manage_templates")

    def perform_create(self, serializer):
        is_public = bool(serializer.validated_data.get("is_public", False))
        if is_public and not self._can_manage_templates():
            raise PermissionDenied("Solo un administrador puede crear una plantilla pública.")
        template = serializer.save(owner=data_owner(self.request.user))
        record_version(
            template, user=self.request.user, note=serializer.validated_data.get("version_note", "")
        )
        if "preview" not in serializer.validated_data:
            refresh_template_thumbnail(template)
        record(
            self.request,
            category="labels",
            action="template.create",
            target=template,
            target_type="labeltemplate",
            target_repr=str(template),
        )

    def _check_can_edit(self, instance, requested_is_public=None):
        user = data_owner(self.request.user)
        is_own_private = instance.owner_id == user.id and not instance.is_public
        if not self._can_manage_templates():
            if not is_own_private:
                raise PermissionDenied("Solo podés editar tus propias plantillas privadas.")
            if requested_is_public:
                raise PermissionDenied(
                    "Solo un administrador puede marcar una plantilla como pública."
                )

    def perform_update(self, serializer):
        instance = serializer.instance
        self._check_can_edit(
            instance, serializer.validated_data.get("is_public", instance.is_public)
        )
        template = serializer.save()
        # Una versión nueva solo si cambió lo que se dibuja (record_version
        # compara con la última): marcarla pública no llena el historial.
        record_version(
            template, user=self.request.user, note=serializer.validated_data.get("version_note", "")
        )
        # La anotación de get_queryset quedó vieja: que el serializer la relea.
        template.current_version = None
        # La miniatura se rehace cuando cambia lo que se dibuja (o si no hay),
        # salvo que el pedido mande su propia imagen.
        data = serializer.validated_data
        drawing_changed = any(key in data for key in ("design", "width_cm", "height_cm"))
        if "preview" not in data and (drawing_changed or not template.preview):
            refresh_template_thumbnail(template)
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
        is_own_private = instance.owner_id == data_owner(request.user).id and not instance.is_public
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
            owner=data_owner(request.user),
            is_public=False,
            width_cm=original.width_cm,
            height_cm=original.height_cm,
            design=original.design,
        )
        current = original.versions.order_by("-number").values_list("number", flat=True).first()
        record_version(
            copy,
            user=request.user,
            note=f"Copia de «{original.name}»" + (f" (versión {current})" if current else ""),
        )
        if original.preview:
            copy.preview.save(
                original.preview.name.rsplit("/", 1)[-1],
                ContentFile(original.preview.read()),
                save=True,
            )
        else:
            refresh_template_thumbnail(copy)
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
        que la vista previa de ``frontend/mis_plantillas.html`` sea fiel al
        resultado real sin necesitar un pedido.
        """
        template = self.get_object()
        computed = build_computed_context(owner=data_owner(request.user), is_preview=True)
        pdf_bytes = render_label_pdf(
            design=template.design,
            width_cm=template.width_cm,
            height_cm=template.height_cm,
            context=SAMPLE_LABEL_CONTEXT,
            computed=computed,
            logo_file=sender_logo(data_owner(request.user)),
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="plantilla-{template.pk}-preview.pdf"'
        return response

    @action(detail=True, methods=["get"])
    def versions(self, request, pk=None):
        """GET /api/v1/labels/templates/<id>/versions/

        El historial completo, de la más nueva a la más vieja, con el diseño
        de cada una (lo necesita la comparación de versiones_plantilla.html).
        Lo ve quien ve la plantilla."""
        template = self.get_object()
        versions = template.versions.select_related("created_by").order_by("-number")
        return Response(LabelTemplateVersionSerializer(versions, many=True).data)

    def _get_version(self, template, number):
        return get_object_or_404(template.versions, number=number)

    @action(detail=True, methods=["get"], url_path=r"versions/(?P<number>\d+)/preview")
    def version_preview(self, request, pk=None, number=None):
        """GET /api/v1/labels/templates/<id>/versions/<n>/preview/ — el PDF de
        ejemplo de esa versión, igual que ``preview`` con la actual."""
        template = self.get_object()
        version = self._get_version(template, number)
        pdf_bytes = render_label_pdf(
            design=version.design,
            width_cm=version.width_cm,
            height_cm=version.height_cm,
            context=SAMPLE_LABEL_CONTEXT,
            computed=build_computed_context(owner=data_owner(request.user), is_preview=True),
        )
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="plantilla-{template.pk}-v{version.number}.pdf"'
        )
        return response

    @action(detail=True, methods=["post"], url_path=r"versions/(?P<number>\d+)/restore")
    def restore_version(self, request, pk=None, number=None):
        """POST /api/v1/labels/templates/<id>/versions/<n>/restore/

        Deja la plantilla como estaba en la versión ``n``, registrado como
        una versión NUEVA (el historial no se reescribe). Mismos permisos que
        editarla. Audita ``template.update``."""
        template = self.get_object()
        self._check_can_edit(template)
        version = self._get_version(template, number)
        previous = template.versions.order_by("-number").values_list("number", flat=True).first()
        restore_version(template, version, user=request.user)
        refresh_template_thumbnail(template)
        record(
            request,
            category="labels",
            action="template.update",
            target=template,
            target_type="labeltemplate",
            target_repr=str(template),
            changes={"version": {"from": previous, "to": f"restaurada la {version.number}"}},
        )
        template = self.get_queryset().get(pk=template.pk)
        return Response(self.get_serializer(template).data)


SEQUENCE_PADDING_MAX = 12


class LabelSequenceView(APIView):
    """GET/PUT /api/v1/labels/sequence/

    La numeración de ``{{secuencia}}`` del usuario (``LabelSequence`` con
    ``key="default"``, la única que usa el render hoy): ``prefix``,
    ``padding`` (ceros a la izquierda) y ``next`` (el número que va a llevar
    el próximo rótulo impreso). ``next`` se guarda como ``current = next - 1``,
    así el contador sigue siendo el mismo que incrementa
    ``LabelSequence.next_value``. Bajar ``next`` permite repetir números: es
    decisión del usuario (p. ej. empezar de nuevo cada año), no se impide."""

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("labels.edit")]

    @staticmethod
    def _payload(seq):
        return {
            "prefix": seq.prefix,
            "padding": seq.padding,
            "next": seq.current + 1,
            "example": f"{seq.prefix}{seq.current + 1:0{seq.padding}d}",
        }

    def get(self, request):
        seq = LabelSequence.objects.filter(owner=data_owner(request.user), key="default").first()
        if seq is None:
            seq = LabelSequence(owner=data_owner(request.user), key="default")
        return Response(self._payload(seq))

    def put(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        errors = {}
        prefix = data.get("prefix", "")
        if not isinstance(prefix, str) or len(prefix.strip()) > 20:
            errors["prefix"] = "Hasta 20 caracteres."
        padding, next_value = data.get("padding"), data.get("next")
        if isinstance(padding, bool) or not isinstance(padding, int) or not 0 <= padding <= SEQUENCE_PADDING_MAX:
            errors["padding"] = f"Un número entero entre 0 y {SEQUENCE_PADDING_MAX}."
        if isinstance(next_value, bool) or not isinstance(next_value, int) or next_value < 1:
            errors["next"] = "Un número entero desde 1."
        if errors:
            raise ValidationError(errors)

        seq, _ = LabelSequence.objects.get_or_create(owner=data_owner(request.user), key="default")
        before = self._payload(seq)
        seq.prefix = prefix.strip()
        seq.padding = padding
        seq.current = next_value - 1
        seq.save(update_fields=["prefix", "padding", "current", "updated_at"])
        after = self._payload(seq)
        changes = {
            key: {"from": before[key], "to": after[key]}
            for key in ("prefix", "padding", "next")
            if before[key] != after[key]
        }
        if changes:
            record(
                request,
                category="labels",
                action="label.update",
                target=seq,
                target_type="label_sequence",
                target_repr=f"Numeración de rótulos ({after['example']})",
                changes=changes,
            )
        return Response(after)


class PreviewLabelView(APIView):
    """POST /api/v1/labels/preview/

    Devuelve el PDF de un diseño que TODAVÍA NO SE GUARDÓ, con datos de
    muestra: ``{"design": {...}, "width_cm": 10, "height_cm": 15}``.

    Existe para que el editor (``editor_rotulos.html``) pueda mostrar lo que
    realmente va a salir impreso en vez de su propia interpretación en CSS.
    El lienzo del editor dibuja con píxeles y el render con medidas
    proporcionales al rótulo, así que cualquier aproximación en el navegador
    se desincroniza tarde o temprano: la única fuente de verdad es este
    render, el mismo que usan la impresión individual y el lote.

    No persiste nada y no necesita un pedido: por eso es un endpoint aparte
    de ``RenderLabelView``, que rinde una plantilla guardada con un pedido
    real.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("labels.render")]

    def post(self, request):
        design = request.data.get("design")
        if not isinstance(design, dict):
            raise ValidationError({"design": "Mandá el diseño como un objeto."})

        dimensions = {}
        for field, default in (("width_cm", 10), ("height_cm", 15)):
            raw = request.data.get(field)
            # Con "or default" un 0 se convertía en el default en vez de
            # rechazarse: 0 es falsy pero es un valor que el cliente mandó.
            if raw in (None, ""):
                raw = default
            try:
                value = float(raw)
            except (TypeError, ValueError):
                raise ValidationError({field: "Debe ser un número."})
            # Mismo rango que acepta LabelTemplate: un rótulo de 0 cm no se
            # imprime y uno de 200 cm no es un rótulo.
            if not 1 <= value <= 100:
                raise ValidationError({field: "Debe estar entre 1 y 100 cm."})
            dimensions[field] = value

        logo_file = None
        template_id = request.data.get("template_id")
        if template_id:
            if not ID_RE.fullmatch(str(template_id).strip()):
                raise ValidationError({"template_id": "Tiene que ser el número de una plantilla."})
            template_id = int(str(template_id).strip())
            # El logo es un archivo del servidor, no viaja en el diseño: si
            # el editor está trabajando sobre una plantilla guardada, se usa
            # el de esa plantilla para que la vista previa lo incluya.
            template = LabelTemplate.objects.filter(
                Q(is_public=True) | Q(owner=data_owner(request.user)), pk=template_id
            ).first()
            logo_file = getattr(template, "logo", None) or None
        else:
            template = None
        if not logo_file:
            # Sin logo propio de la plantilla, el del comercio (remitente).
            try:
                logo_file = sender_logo(data_owner(request.user))
            except NoTeam:  # el admin sin comercio no tiene logo que usar
                pass

        # Mismo permiso que al guardar: solo imágenes del comercio, de
        # plantillas públicas o de la plantilla que se está editando.
        from .design_shapes import image_ids

        used = image_ids(design)
        if used and used - label_images.allowed_image_ids(
            request.user, [template.design] if template is not None else []
        ):
            raise ValidationError({"design": "Una de las imágenes del diseño no existe en tu comercio."})

        warnings = []
        pdf_bytes = render_label_pdf(
            design,
            dimensions["width_cm"],
            dimensions["height_cm"],
            context=build_preview_context(),
            logo_file=logo_file,
            warnings=warnings,
        )
        if request.data.get("thermal") is True:
            # Vista térmica del editor: el mismo dibujo en 1 bit a 203 dpi.
            from .thermal_preview import thermal_png

            response = HttpResponse(thermal_png(pdf_bytes), content_type="image/png")
        else:
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = 'inline; filename="vista-previa.pdf"'
        # Avisos del dibujo como "código:ancho mínimo en cm" (hoy, un código
        # de barras que con el tracking de muestra, de 19 dígitos, no entra
        # legible en su recuadro). El editor los traduce a un mensaje.
        if warnings:
            response["X-Label-Warnings"] = ",".join(
                "%s:%s" % (warning["code"], warning.get("min_width_cm", warning.get("field", ""))) for warning in warnings
            )
        return response


class PreviewContextView(APIView):
    """GET /api/v1/labels/preview-context/

    Los datos de muestra que usa la vista previa, para que el lienzo del
    editor muestre el mismo texto que el PDF: ``context`` (datos del envío)
    y ``computed`` (variables calculadas, ya resueltas). Sin sesión de
    comercio ``data_owner`` responde 403 ``no_team`` como el resto."""

    COMPUTED_KEYS = ("fecha", "hora", "fecha_hora", "secuencia", "bulto", "bultos", "bulto_de_bultos")

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("labels.render")]

    def get(self, request):
        resolver = build_computed_context(
            owner=data_owner(request.user), is_preview=True, bulto=1, bultos=2
        )
        return Response(
            {
                "context": build_preview_context(),
                "computed": {key: resolver(key) or "" for key in self.COMPUTED_KEYS},
            }
        )


class RenderLabelView(APIView):
    """POST /api/v1/labels/render/

    Render SIN persistir: arma el rótulo con el diseño de una plantilla y
    los datos de un pedido concreto ``{"template_id": N, "order_id": M}``.
    Es la semilla del render por lote — simple y sin estado, no crea ningún
    ``Label``.

    ``format`` elige la salida: ``"pdf"`` (por defecto, sirve en cualquier
    impresora) o ``"zpl"`` para una térmica Zebra, donde el texto y los
    códigos son comandos nativos en vez de una página rasterizada. Con
    ``zpl``, ``dpmm`` es la densidad de la impresora (8 = 203 dpi, la
    mayoría; 12 = 300 dpi): el mismo diseño se emite con los dots que
    correspondan, así que errarle no rompe nada pero saca la etiqueta a
    otra escala. Ver ``apps.labels.zpl``.
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
            LabelTemplate.objects.filter(Q(is_public=True) | Q(owner=data_owner(request.user))),
            pk=template_id,
        )

        # El pedido debe ser del usuario, salvo que sea admin con
        # 'labels.view_all' (mismo criterio que el resto de la app: la
        # identidad siempre sale de request.user, nunca de un id del cliente).
        if user_has_permission(request.user, "labels.view_all"):
            order_qs = Order.objects.all()
        else:
            order_qs = Order.objects.filter(order_scope_q(request.user), user=data_owner(request.user))
        order = get_object_or_404(
            order_qs.select_related("address", "user"), pk=order_id
        )

        output_format = str(request.data.get("format") or "pdf").strip().lower()
        if output_format not in ("pdf", "zpl"):
            raise ValidationError({"format": "Debe ser 'pdf' o 'zpl'."})

        context = build_label_context(order=order)
        # /labels/render/ es una vista previa sin persistir (ver el
        # docstring de la clase): no consume la numeración secuencial real,
        # el flag es explícito acá, no se adivina del request.
        computed = build_computed_context(owner=data_owner(request.user), is_preview=True)

        if output_format == "zpl":
            content = render_label_zpl(
                design=template.design,
                width_cm=template.width_cm,
                height_cm=template.height_cm,
                context=context,
                computed=computed,
                dpmm=_parse_dpmm(request.data.get("dpmm")),
            )
            content_type = "text/plain; charset=utf-8"
            extension = "zpl"
        else:
            content = render_label_pdf(
                design=template.design,
                width_cm=template.width_cm,
                height_cm=template.height_cm,
                context=context,
                computed=computed,
            )
            content_type = "application/pdf"
            extension = "pdf"

        record(
            request,
            category="labels",
            action="label.render",
            target=order,
            target_type="order",
            target_repr=str(order),
            changes={
                "template": {"from": None, "to": template.pk},
                "format": {"from": None, "to": output_format},
            },
        )
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = (
            f'attachment; filename="rotulo-plantilla-{template.pk}-pedido-{order.pk}.{extension}"'
        )
        return response


def _parse_dpmm(raw):
    """La densidad pedida, validada. Sin valor, la de una impresora de 203
    dpi, que es la que tiene la enorme mayoría del parque."""
    if raw in (None, ""):
        return DEFAULT_DPMM
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({"dpmm": "'dpmm' debe ser un número entero."})
    if value not in SUPPORTED_DPMM:
        raise ValidationError(
            {
                "dpmm": "Densidad no soportada: %s (válidas: %s)."
                % (value, ", ".join(str(item) for item in SUPPORTED_DPMM))
            }
        )
    return value


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
    ``apps.labels.label_rendering.build_qr_drawing``/``build_barcode_drawing``
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
