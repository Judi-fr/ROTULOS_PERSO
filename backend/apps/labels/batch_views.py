"""Generación de rótulos por lote: un ``Document`` con muchos rótulos.

Sin cola de tareas (Celery/Redis, ver CLAUDE.md): corre SÍNCRONO, en el
mismo request. Para que eso sea sostenible: ``LABELS_BATCH_MAX_ITEMS``
acota el tamaño del lote, y el ``Document`` se crea en ``processing``
ANTES de generar nada, así el frontend ya tiene un id para mostrar (y un
registro que sobrevive) aunque el proceso tarde o falle.

Un solo camino de generación: cada rótulo pasa por
``apps.labels.rendering.draw_label_page``/``render_label_pdf``, las
mismas funciones que usa el endpoint individual — acá no se reimplementa
el dibujo, solo se decide QUÉ rótulos entran y cómo se empaquetan
(un PDF de muchas páginas, o un ZIP de un PDF por rótulo).
"""

from __future__ import annotations

import zipfile
from datetime import datetime
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.db.models import Q
from django.utils import timezone
from reportlab.pdfgen import canvas as pdf_canvas
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions_map import user_has_permission
from apps.accounts.role_permissions import HasRolePermission
from apps.audit.services import record
from apps.documents.models import Document
from apps.documents.serializers import DocumentSerializer
from apps.orders.models import Order

from .models import Label, LabelTemplate
from .rendering import CM_TO_POINTS, build_label_context, draw_label_page, render_label_pdf

VALID_OUTPUTS = {"pdf", "zip"}
SELECTOR_KEYS = ("order_ids", "label_ids", "filters")


def _parse_date(raw, field_label):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise ValidationError(
            {field_label: f"Formato de fecha inválido (usar YYYY-MM-DD): {raw!r}."}
        )


def _as_int_list(value, field_label):
    if not isinstance(value, list) or not value:
        raise ValidationError({field_label: "Debe ser una lista de ids no vacía."})
    try:
        return [int(v) for v in value]
    except (TypeError, ValueError):
        raise ValidationError({field_label: "Debe ser una lista de ids enteros."})


def _render_params_for_item(item, template):
    """Datos de dibujo para UN ítem del lote (``Label`` u ``Order``), y un
    nombre legible para identificarlo en el ZIP / en los mensajes de
    ítems omitidos. Mismo criterio que ``views.LabelViewSet.pdf`` (rótulo
    propio) y ``views.RenderLabelView`` (plantilla + pedido)."""
    if isinstance(item, Label):
        context = build_label_context(order=item.order, label=item) if item.order else {}
        name_hint = context.get("tracking") or f"rotulo-{item.pk}"
        return item.design, item.width_cm, item.height_cm, context, (item.logo or None), name_hint

    # Order: el diseño sale de la plantilla del lote, sin logo propio
    # (igual que RenderLabelView).
    context = build_label_context(order=item)
    name_hint = context.get("tracking") or f"pedido-{item.pk}"
    return template.design, template.width_cm, template.height_cm, context, None, name_hint


def _safe_zip_name(name_hint, used_names):
    base = "".join(c for c in name_hint if c.isalnum() or c in "-_") or "rotulo"
    candidate = f"{base}.pdf"
    n = 2
    while candidate in used_names:
        candidate = f"{base}-{n}.pdf"
        n += 1
    used_names.add(candidate)
    return candidate


def _render_combined_pdf(items, template):
    """Un PDF con una página por ítem, sobre el MISMO canvas (no arma N
    PDFs para concatenarlos después: con un lote grande la diferencia de
    memoria es real). Un ítem que falla se salta sin dejar una página
    a medio dibujar: como todavía no se confirmó con ``showPage()``, se
    descarta lo que se llegó a dibujar de ese ítem antes de seguir."""
    buffer = BytesIO()
    pdf = None
    item_count = 0
    skipped = []
    page_has_content = False

    for item in items:
        design, width_cm, height_cm, context, logo_file, name_hint = _render_params_for_item(
            item, template
        )
        width_pt = float(width_cm) * CM_TO_POINTS
        height_pt = float(height_cm) * CM_TO_POINTS

        if pdf is None:
            pdf = pdf_canvas.Canvas(buffer, pagesize=(width_pt, height_pt))
        else:
            if page_has_content:
                pdf.showPage()
            pdf.setPageSize((width_pt, height_pt))

        # Nada se confirma en el PDF hasta el próximo showPage(): si este
        # ítem falla a mitad de dibujo, se descarta lo ya escrito en el
        # buffer de la página actual (atributo interno de ReportLab, pero
        # es la única forma de "deshacer" un dibujo parcial sin armar un
        # PDF aparte para descartarlo).
        code_snapshot = len(pdf._code)
        try:
            draw_label_page(pdf, design, width_cm, height_cm, context=context, logo_file=logo_file)
        except Exception as exc:  # noqa: BLE001 - un ítem roto no tumba el lote
            del pdf._code[code_snapshot:]
            # Página en blanco otra vez: si el ÚLTIMO ítem es el que
            # falla, este flag es lo único que evita que el showPage()
            # final de más abajo la confirme como una página extra vacía.
            page_has_content = False
            skipped.append(f"{name_hint}: {exc}")
            continue

        item_count += 1
        page_has_content = True

    if pdf is not None:
        if page_has_content:
            pdf.showPage()
        pdf.save()
    return buffer.getvalue(), item_count, skipped


def _render_zip(items, template):
    """Un ZIP con un PDF por ítem (``render_label_pdf``, el mismo wrapper
    que usa el endpoint individual), nombrados por el código de
    seguimiento."""
    buffer = BytesIO()
    item_count = 0
    skipped = []
    used_names = set()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in items:
            design, width_cm, height_cm, context, logo_file, name_hint = _render_params_for_item(
                item, template
            )
            try:
                pdf_bytes = render_label_pdf(
                    design, width_cm, height_cm, context=context, logo_file=logo_file
                )
            except Exception as exc:  # noqa: BLE001 - un ítem roto no tumba el lote
                skipped.append(f"{name_hint}: {exc}")
                continue
            zf.writestr(_safe_zip_name(name_hint, used_names), pdf_bytes)
            item_count += 1

    return buffer.getvalue(), item_count, skipped


class LabelBatchView(APIView):
    """POST /api/v1/labels/batch/

    ``{"template_id": N, "order_ids": [...], "output": "pdf"}`` (o
    ``label_ids``/``filters`` en vez de ``order_ids`` — exactamente una de
    las tres). Devuelve 202 con el ``Document`` recién creado.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("labels.batch")]

    def post(self, request):
        data = request.data
        output = str(data.get("output") or "pdf").strip().lower()
        if output not in VALID_OUTPUTS:
            raise ValidationError({"output": "Debe ser 'pdf' o 'zip'."})

        # "in data" (no la verdad del valor): un 'filters' vacío ({}) es
        # una selección válida ("todos mis pedidos", sin recorte), no la
        # ausencia del selector.
        present = [key for key in SELECTOR_KEYS if key in data]
        if len(present) != 1:
            raise ValidationError(
                {
                    "detail": (
                        "Elegí exactamente una forma de seleccionar qué imprimir: "
                        "'order_ids', 'label_ids' o 'filters'."
                    )
                }
            )
        selector = present[0]
        can_view_all = user_has_permission(request.user, "labels.view_all")

        template = None
        if selector == "label_ids":
            items = self._resolve_labels(request.user, data["label_ids"], can_view_all)
        else:
            template = self._resolve_template(request.user, data.get("template_id"))
            if selector == "order_ids":
                items = self._resolve_orders(request.user, data["order_ids"], can_view_all)
            else:
                items = self._resolve_orders_by_filters(request.user, data["filters"], can_view_all)

        if not items:
            raise ValidationError({"detail": "No hay elementos para generar."})

        max_items = getattr(settings, "LABELS_BATCH_MAX_ITEMS", 200)
        if len(items) > max_items:
            raise ValidationError(
                {
                    "detail": (
                        f"El lote supera el máximo de {max_items} rótulos "
                        f"(pediste {len(items)})."
                    )
                }
            )

        document = Document.objects.create(
            user=request.user,
            name=f"Rótulos {timezone.localdate().strftime('%d/%m')} ({len(items)} envíos)",
            kind=Document.Kind.LABELS_BATCH,
            status=Document.Status.PROCESSING,
        )
        record(
            request,
            category="labels",
            action="label.batch",
            target=document,
            target_type="document",
            target_repr=str(document),
            changes={"item_count": {"from": None, "to": len(items)}, "output": {"from": None, "to": output}},
        )

        self._run_batch(document, items, template, output)

        return Response(
            DocumentSerializer(document, context={"request": request}).data,
            status=status.HTTP_202_ACCEPTED,
        )

    # --- Resolución de la selección ------------------------------------

    def _resolve_template(self, user, template_id):
        if not template_id:
            raise ValidationError(
                {"template_id": "Es obligatorio junto con 'order_ids'/'filters'."}
            )
        # Mismo criterio que RenderLabelView: pública o propia (una
        # plantilla no es un dato privado de OTRO usuario en el mismo
        # sentido que un pedido/rótulo, así que no hay bypass de admin acá).
        try:
            return LabelTemplate.objects.get(Q(is_public=True) | Q(owner=user), pk=template_id)
        except LabelTemplate.DoesNotExist:
            raise ValidationError(
                {"template_id": f"La plantilla {template_id!r} no existe o no es tuya."}
            )

    def _resolve_labels(self, user, label_ids, can_view_all):
        label_ids = _as_int_list(label_ids, "label_ids")
        queryset = Label.objects.filter(pk__in=label_ids, is_active=True)
        if not can_view_all:
            queryset = queryset.filter(user=user)
        found = {label.pk: label for label in queryset}
        missing = [str(i) for i in label_ids if i not in found]
        if missing:
            raise ValidationError(
                {
                    "label_ids": (
                        f"Los siguientes rótulos no existen o no son tuyos: {', '.join(missing)}."
                    )
                }
            )
        return [found[i] for i in label_ids]

    def _resolve_orders(self, user, order_ids, can_view_all):
        order_ids = _as_int_list(order_ids, "order_ids")
        queryset = Order.objects.select_related("address", "user").filter(pk__in=order_ids)
        if not can_view_all:
            queryset = queryset.filter(user=user)
        found = {order.pk: order for order in queryset}
        missing = [str(i) for i in order_ids if i not in found]
        if missing:
            raise ValidationError(
                {
                    "order_ids": (
                        f"Los siguientes pedidos no existen o no son tuyos: {', '.join(missing)}."
                    )
                }
            )
        return [found[i] for i in order_ids]

    def _resolve_orders_by_filters(self, user, filters, can_view_all):
        if not isinstance(filters, dict):
            raise ValidationError({"filters": "Debe ser un objeto."})

        queryset = Order.objects.select_related("address", "user").all()
        if not can_view_all:
            queryset = queryset.filter(user=user)

        status_param = str(filters.get("status") or "").strip()
        if status_param:
            queryset = queryset.filter(status=status_param)

        date_from = filters.get("date_from")
        date_to = filters.get("date_to")
        if date_from and date_to and _parse_date(date_from, "date_from") > _parse_date(
            date_to, "date_to"
        ):
            raise ValidationError({"date_to": "'date_to' no puede ser anterior a 'date_from'."})
        if date_from:
            queryset = queryset.filter(created_at__date__gte=_parse_date(date_from, "date_from"))
        if date_to:
            queryset = queryset.filter(created_at__date__lte=_parse_date(date_to, "date_to"))

        return list(queryset.order_by("id"))

    # --- Generación ------------------------------------------------------

    def _run_batch(self, document, items, template, output):
        try:
            if output == "zip":
                file_bytes, item_count, skipped = _render_zip(items, template)
                filename = f"rotulos-{document.pk}.zip"
            else:
                file_bytes, item_count, skipped = _render_combined_pdf(items, template)
                filename = f"rotulos-{document.pk}.pdf"

            if item_count == 0:
                document.status = Document.Status.FAILED
                document.error_message = "No se pudo generar ningún rótulo. " + "; ".join(skipped)
                document.save(update_fields=["status", "error_message", "updated_at"])
                return

            document.file.save(filename, ContentFile(file_bytes), save=False)
            document.item_count = item_count
            document.size_bytes = len(file_bytes)
            document.status = Document.Status.READY
            if skipped:
                document.error_message = (
                    f"{len(skipped)} rótulo(s) omitido(s) de {len(items)}: " + "; ".join(skipped)
                )
            document.save()
        except Exception as exc:  # noqa: BLE001 - un lote no puede quedar a medio camino
            document.status = Document.Status.FAILED
            document.error_message = str(exc)
            document.save(update_fields=["status", "error_message", "updated_at"])
