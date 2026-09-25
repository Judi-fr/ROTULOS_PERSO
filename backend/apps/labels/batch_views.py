"""Generación de rótulos por lote: un ``Document`` con muchos rótulos.

Sin cola de tareas (Celery/Redis, ver CLAUDE.md): corre SÍNCRONO, en el
mismo request. Para que eso sea sostenible: ``LABELS_BATCH_MAX_ITEMS``
acota el tamaño del lote, y el ``Document`` se crea en ``processing``
ANTES de generar nada, así el frontend ya tiene un id para mostrar (y un
registro que sobrevive) aunque el proceso tarde o falle.

Un solo camino de generación: cada rótulo pasa por
``apps.labels.label_rendering.draw_label_page``/``render_label_pdf``, las
mismas funciones que usa el endpoint individual — acá no se reimplementa
el dibujo, solo se decide QUÉ rótulos entran y cómo se empaquetan
(un PDF de muchas páginas, o un ZIP de un PDF por rótulo).
"""

from __future__ import annotations

import zipfile
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
from apps.common.date_filters import date_range_q
from apps.documents.models import Document
from apps.documents.serializers import DocumentSerializer
from apps.orders.models import Order

from .models import Label, LabelTemplate
from .zpl import DEFAULT_DPMM, SUPPORTED_DPMM, render_label_zpl
from .label_rendering import (
    CM_TO_POINTS,
    build_computed_context,
    build_label_context,
    compute_a4_grid,
    draw_label_page,
    render_label_pdf,
)

VALID_OUTPUTS = {"pdf", "zip", "zpl"}
VALID_PAGE_LAYOUTS = {"label", "a4"}
SELECTOR_KEYS = ("order_ids", "label_ids", "filters")


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

    # Order: el diseño sale de la plantilla del lote. El logo, de la tienda
    # del pedido si la cargó (cada tienda imprime el suyo); un pedido sin
    # tienda, o una tienda sin logo, sale sin logo como antes.
    context = build_label_context(order=item)
    name_hint = context.get("tracking") or f"pedido-{item.pk}"
    store = getattr(item, "store_connection", None)
    logo_file = getattr(store, "logo", None) or None
    return template.design, template.width_cm, template.height_cm, context, logo_file, name_hint


def _safe_zip_name(name_hint, used_names):
    base = "".join(c for c in name_hint if c.isalnum() or c in "-_") or "rotulo"
    candidate = f"{base}.pdf"
    n = 2
    while candidate in used_names:
        candidate = f"{base}-{n}.pdf"
        n += 1
    used_names.add(candidate)
    return candidate


def _parse_dpmm(raw, default=DEFAULT_DPMM):
    """La densidad de la impresora térmica, validada. ``default`` es lo que
    se devuelve si no vino nada: en el lote es ``None``, porque ahí el
    siguiente paso es mirar la tienda antes de caer al valor por defecto."""
    if raw in (None, ""):
        return default
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


def _render_combined_zpl(items, template, owner=None, dpmm=DEFAULT_DPMM):
    """Un solo archivo ZPL con todas las etiquetas, una atrás de otra.

    No hay equivalente del layout A4: una térmica no tiene hoja que
    aprovechar, el rollo ya viene troquelado a la medida de la etiqueta y
    cada ``^XA..^XZ`` avanza una. Un ítem que falla se salta y se informa,
    igual que en el PDF — acá es más simple todavía porque cada etiqueta es
    un texto independiente y no hay nada a medio dibujar que descartar.
    """
    partes = []
    skipped = []
    total = len(items)

    for bulto, item in enumerate(items, start=1):
        design, width_cm, height_cm, context, logo_file, name_hint = _render_params_for_item(
            item, template
        )
        computed = build_computed_context(owner=owner, bulto=bulto, bultos=total)
        try:
            partes.append(
                render_label_zpl(
                    design=design,
                    width_cm=width_cm,
                    height_cm=height_cm,
                    context=context,
                    logo_file=logo_file,
                    computed=computed,
                    dpmm=dpmm,
                )
            )
        except Exception as exc:  # noqa: BLE001 - un ítem roto no tumba el lote
            skipped.append(f"{name_hint}: {exc}")

    return "\n".join(partes).encode("utf-8"), len(partes), skipped


def _render_combined_pdf(items, template, owner=None):
    """Un PDF con una página por ítem, sobre el MISMO canvas (no arma N
    PDFs para concatenarlos después: con un lote grande la diferencia de
    memoria es real). Un ítem que falla se salta sin dejar una página
    a medio dibujar: como todavía no se confirmó con ``showPage()``, se
    descarta lo que se llegó a dibujar de ese ítem antes de seguir.

    ``owner`` + la posición de cada ítem alimentan las variables calculadas
    de la Historia 29 (``{{bulto}}``/``{{bultos}}``/``{{secuencia}}``): un
    valor de secuencia por RÓTULO, no uno por lote entero — ver
    ``build_computed_context``, se construye un resolver nuevo por ítem."""
    buffer = BytesIO()
    pdf = None
    item_count = 0
    skipped = []
    page_has_content = False
    total = len(items)

    for bulto, item in enumerate(items, start=1):
        design, width_cm, height_cm, context, logo_file, name_hint = _render_params_for_item(
            item, template
        )
        computed = build_computed_context(owner=owner, bulto=bulto, bultos=total)
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
            draw_label_page(
                pdf, design, width_cm, height_cm, context=context, logo_file=logo_file, computed=computed
            )
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


def _draw_cut_lines(pdf, x_pt, y_pt, w_pt, h_pt):
    """Línea de corte punteada fina alrededor de un rótulo en una hoja A4
    (ver ``_render_combined_pdf_a4``): coordenadas ABSOLUTAS de la hoja
    (no las del rótulo), se dibuja antes de trasladar el canvas."""
    pdf.saveState()
    pdf.setDash(2, 2)
    pdf.setLineWidth(0.4)
    pdf.setStrokeColorRGB(0.6, 0.6, 0.6)
    pdf.rect(x_pt, y_pt, w_pt, h_pt, stroke=1, fill=0)
    pdf.restoreState()


def _render_combined_pdf_a4(items, template, owner=None):
    """Igual que ``_render_combined_pdf`` (un PDF, varias páginas, mismo
    canvas), pero acomoda varios rótulos por hoja A4 en vez de una página
    del tamaño exacto del rótulo (ver ``rendering.compute_a4_grid``) —
    pensado para imprimir en una impresora de oficina común en vez de una
    térmica de etiquetas. El dibujo de cada rótulo sigue siendo
    EXACTAMENTE ``draw_label_page``: lo único que cambia es dónde se
    traslada el origen del canvas antes de llamarlo.

    Asume que todos los ítems miden igual (el tamaño del primero define la
    grilla): un ítem de otra medida no entraría en las mismas casillas, así
    que se omite igual que un ítem que falla al dibujarse.

    ``owner`` — ver ``_render_combined_pdf``: un valor de secuencia por
    RÓTULO, con su posición real dentro del LOTE (no de la hoja).
    """
    if not items:
        return b"", 0, []

    first_width, first_height = _render_params_for_item(items[0], template)[1:3]
    grid = compute_a4_grid(first_width, first_height)
    if grid["per_page"] == 0:
        raise ValidationError(
            {
                "detail": (
                    f"Un rótulo de {first_width}x{first_height} cm no entra en una "
                    "hoja A4 con los márgenes actuales."
                )
            }
        )

    page_w_pt = grid["page_width_cm"] * CM_TO_POINTS
    page_h_pt = grid["page_height_cm"] * CM_TO_POINTS
    label_w_pt = float(first_width) * CM_TO_POINTS
    label_h_pt = float(first_height) * CM_TO_POINTS

    buffer = BytesIO()
    pdf = pdf_canvas.Canvas(buffer, pagesize=(page_w_pt, page_h_pt))
    item_count = 0
    skipped = []
    slot_index = 0
    total = len(items)

    for bulto, item in enumerate(items, start=1):
        design, width_cm, height_cm, context, logo_file, name_hint = _render_params_for_item(
            item, template
        )
        computed = build_computed_context(owner=owner, bulto=bulto, bultos=total)
        if float(width_cm) != float(first_width) or float(height_cm) != float(first_height):
            skipped.append(
                f"{name_hint}: mide {width_cm}x{height_cm} cm, distinto al resto del lote "
                f"({first_width}x{first_height} cm) — no entra en la misma grilla A4."
            )
            continue

        if slot_index >= grid["per_page"]:
            pdf.showPage()
            pdf.setPageSize((page_w_pt, page_h_pt))
            slot_index = 0

        x_cm, y_cm = grid["positions"][slot_index]
        x_pt = x_cm * CM_TO_POINTS
        y_bottom_pt = page_h_pt - (y_cm * CM_TO_POINTS) - label_h_pt

        # Igual criterio que _render_combined_pdf: si el dibujo falla a
        # mitad de camino, se descarta lo ya escrito de ESTE ítem (nunca
        # arrastra un rótulo a medio dibujar), sin tocar los demás que ya
        # están en la misma hoja.
        code_snapshot = len(pdf._code)
        try:
            _draw_cut_lines(pdf, x_pt, y_bottom_pt, label_w_pt, label_h_pt)
            pdf.saveState()
            pdf.translate(x_pt, y_bottom_pt)
            draw_label_page(
                pdf, design, width_cm, height_cm, context=context, logo_file=logo_file, computed=computed
            )
            pdf.restoreState()
        except Exception as exc:  # noqa: BLE001 - un ítem roto no tumba el lote
            del pdf._code[code_snapshot:]
            skipped.append(f"{name_hint}: {exc}")
            continue

        slot_index += 1
        item_count += 1

    if item_count > 0:
        pdf.showPage()
        pdf.save()
    return buffer.getvalue(), item_count, skipped


def _render_zip(items, template, owner=None):
    """Un ZIP con un PDF por ítem (``render_label_pdf``, el mismo wrapper
    que usa el endpoint individual), nombrados por el código de
    seguimiento. ``owner`` — ver ``_render_combined_pdf``."""
    buffer = BytesIO()
    item_count = 0
    skipped = []
    used_names = set()
    total = len(items)

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for bulto, item in enumerate(items, start=1):
            design, width_cm, height_cm, context, logo_file, name_hint = _render_params_for_item(
                item, template
            )
            computed = build_computed_context(owner=owner, bulto=bulto, bultos=total)
            try:
                pdf_bytes = render_label_pdf(
                    design, width_cm, height_cm, context=context, logo_file=logo_file, computed=computed
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

    ``output``: ``"pdf"`` (uno solo, una página por rótulo), ``"zip"`` (un
    PDF por rótulo) o ``"zpl"`` (un archivo para mandarle crudo a una
    térmica Zebra). Con ``zpl``, ``dpmm`` es la densidad de la impresora
    (8 = 203 dpi por defecto, 12 = 300) y ``page_layout`` se ignora: no hay
    hoja que aprovechar, el rollo ya viene troquelado.
    """

    def get_permissions(self):
        return [IsAuthenticated(), HasRolePermission("labels.batch")]

    def post(self, request):
        data = request.data
        output = str(data.get("output") or "pdf").strip().lower()
        if output not in VALID_OUTPUTS:
            raise ValidationError({"output": "Debe ser 'pdf', 'zip' o 'zpl'."})
        # Solo se usa con output="zpl"; se valida igual si vino, para no
        # aceptar en silencio un valor que el usuario creía que aplicaba.
        # None = no lo pidió: más abajo puede salir de la tienda.
        requested_dpmm = _parse_dpmm(data.get("dpmm"), default=None)

        page_layout = str(data.get("page_layout") or "label").strip().lower()
        if page_layout not in VALID_PAGE_LAYOUTS:
            raise ValidationError({"page_layout": "Debe ser 'label' o 'a4'."})

        # Nunca la verdad del valor (un booleano False mandado explícito
        # tiene que seguir siendo False, no "ausente"): default False.
        skip_existing = bool(data.get("skip_existing", False))

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
        skipped_existing_count = 0
        if selector == "label_ids":
            items = self._resolve_labels(request.user, data["label_ids"], can_view_all)
        else:
            if selector == "order_ids":
                items, skipped_existing_count = self._resolve_orders(
                    request.user, data["order_ids"], can_view_all, skip_existing
                )
            else:
                items, skipped_existing_count = self._resolve_orders_by_filters(
                    request.user, data["filters"], can_view_all, skip_existing
                )
            # Después de los pedidos: sin template_id, la plantilla puede
            # salir de la tienda de esos pedidos (ver _resolve_template).
            template = self._resolve_template(request.user, data.get("template_id"), items)

        if not items:
            if skipped_existing_count:
                raise ValidationError(
                    {
                        "detail": (
                            f"Los {skipped_existing_count} pedido(s) seleccionados ya tienen "
                            "un rótulo generado (skip_existing)."
                        )
                    }
                )
            raise ValidationError({"detail": "No hay elementos para generar."})

        # Igual que la plantilla: sin dpmm explícito, puede salir de la
        # tienda de los pedidos del lote (ver _resolve_dpmm).
        dpmm = self._resolve_dpmm(requested_dpmm, items)

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
            changes={
                "item_count": {"from": None, "to": len(items)},
                "output": {"from": None, "to": output},
                "page_layout": {"from": None, "to": page_layout},
            },
        )

        self._run_batch(
            document, items, template, output, page_layout, skipped_existing_count, dpmm
        )

        return Response(
            DocumentSerializer(document, context={"request": request}).data,
            status=status.HTTP_202_ACCEPTED,
        )

    # --- Resolución de la selección ------------------------------------

    def _resolve_dpmm(self, requested, items):
        """La densidad a usar para el lote.

        Misma regla que ``_resolve_template``: lo que pidió el usuario manda;
        si no pidió nada y TODOS los pedidos son de tiendas que configuraron
        la MISMA densidad, se usa esa. Con tiendas que configuraron
        densidades distintas no hay una respuesta correcta, así que cae al
        valor por defecto (203 dpi) en vez de elegir una al azar.
        """
        if requested is not None:
            return requested
        con_tienda = [
            item for item in items if getattr(item, "store_connection", None) is not None
        ]
        densidades = {
            item.store_connection.label_printer_dpmm
            for item in con_tienda
            if item.store_connection.label_printer_dpmm
        }
        if len(densidades) == 1 and len(con_tienda) == len(items):
            return densidades.pop()
        return DEFAULT_DPMM

    def _resolve_template(self, user, template_id, orders=None):
        if not template_id:
            # Sin template_id: si todos los pedidos del lote son de tiendas
            # que eligieron la MISMA plantilla preferida, se usa esa. Con
            # tiendas distintas (o pedidos sin tienda) no se puede elegir por
            # el usuario: cae a la pública por defecto, abajo.
            store_templates = {
                order.store_connection.default_template
                for order in (orders or [])
                if getattr(order, "store_connection", None) is not None
                and order.store_connection.default_template_id is not None
            }
            if len(store_templates) == 1 and len(store_templates) == len(
                [order for order in (orders or []) if getattr(order, "store_connection", None)]
            ):
                preferred = store_templates.pop()
                if preferred.is_active and (preferred.is_public or preferred.owner_id == user.pk):
                    return preferred
            # Sin template_id: cae a la plantilla pública "por defecto"
            # (la más antigua activa — normalmente la sembrada por
            # apps.labels.migrations.0003_seed_default_label_template) en
            # vez de exigirle a cada usuario que cree una antes de poder
            # usar el lote.
            template = (
                LabelTemplate.objects.filter(is_public=True, is_active=True).order_by("id").first()
            )
            if template is None:
                raise ValidationError(
                    {
                        "template_id": (
                            "No hay ninguna plantilla pública disponible: pasá 'template_id' "
                            "con una propia, o creá/publicá una plantilla primero."
                        )
                    }
                )
            return template
        # Mismo criterio que RenderLabelView: pública o propia (una
        # plantilla no es un dato privado de OTRO usuario en el mismo
        # sentido que un pedido/rótulo, así que no hay bypass de admin acá).
        try:
            return LabelTemplate.objects.get(
                Q(is_public=True) | Q(owner=user), pk=template_id, is_active=True
            )
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

    def _filter_skip_existing(self, orders, skip_existing):
        """Si ``skip_existing``, saca de ``orders`` los que ya tienen un
        ``Label`` activo asociado (correr el mismo lote dos veces no debe
        volver a imprimir todo sin avisar). Devuelve ``(orders, cuántos se
        saltearon)`` — el llamador decide qué hacer con ese conteo (ver
        ``post``/``_run_batch``, que lo suma al ``error_message`` del
        documento junto con los ítems omitidos por error)."""
        if not skip_existing or not orders:
            return orders, 0
        order_ids_with_label = set(
            Label.objects.filter(order_id__in=[o.pk for o in orders], is_active=True)
            .values_list("order_id", flat=True)
            .distinct()
        )
        if not order_ids_with_label:
            return orders, 0
        remaining = [o for o in orders if o.pk not in order_ids_with_label]
        return remaining, len(orders) - len(remaining)

    def _resolve_orders(self, user, order_ids, can_view_all, skip_existing=False):
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
        orders = [found[i] for i in order_ids]
        return self._filter_skip_existing(orders, skip_existing)

    def _resolve_orders_by_filters(self, user, filters, can_view_all, skip_existing=False):
        if not isinstance(filters, dict):
            raise ValidationError({"filters": "Debe ser un objeto."})

        queryset = Order.objects.select_related("address", "user").all()
        if not can_view_all:
            queryset = queryset.filter(user=user)

        status_param = str(filters.get("status") or "").strip()
        if status_param:
            queryset = queryset.filter(status=status_param)

        # `filters` llega en el body JSON y no en la query string, pero el
        # contrato de las fechas es el mismo que el de los listados.
        queryset = queryset.filter(date_range_q(filters))

        orders = list(queryset.order_by("id"))
        return self._filter_skip_existing(orders, skip_existing)

    # --- Generación ------------------------------------------------------

    def _run_batch(
        self,
        document,
        items,
        template,
        output,
        page_layout,
        skipped_existing_count=0,
        dpmm=DEFAULT_DPMM,
    ):
        # Dueño de la numeración secuencial del lote (Historia 29): el
        # ``Document`` ya se creó con ``user=request.user`` (ver ``post``),
        # así que sale de ahí en vez de threadear ``request`` hasta acá.
        owner = document.user
        try:
            if output == "zip":
                # El layout A4 (varios rótulos por hoja) no tiene sentido
                # para un ZIP (cada entrada ya es su propio PDF de una
                # página): se ignora en silencio, no es un error del
                # usuario.
                file_bytes, item_count, skipped = _render_zip(items, template, owner=owner)
                filename = f"rotulos-{document.pk}.zip"
            elif output == "zpl":
                file_bytes, item_count, skipped = _render_combined_zpl(
                    items, template, owner=owner, dpmm=dpmm
                )
                filename = f"rotulos-{document.pk}.zpl"
            elif page_layout == "a4":
                file_bytes, item_count, skipped = _render_combined_pdf_a4(items, template, owner=owner)
                filename = f"rotulos-{document.pk}.pdf"
            else:
                file_bytes, item_count, skipped = _render_combined_pdf(items, template, owner=owner)
                filename = f"rotulos-{document.pk}.pdf"

            notes = []
            if skipped_existing_count:
                notes.append(
                    f"{skipped_existing_count} pedido(s) omitido(s) por ya tener un rótulo "
                    "generado (skip_existing)."
                )

            if item_count == 0:
                document.status = Document.Status.FAILED
                notes.append("No se pudo generar ningún rótulo. " + "; ".join(skipped))
                document.error_message = " ".join(notes)
                document.save(update_fields=["status", "error_message", "updated_at"])
                return

            document.file.save(filename, ContentFile(file_bytes), save=False)
            document.item_count = item_count
            document.size_bytes = len(file_bytes)
            document.status = Document.Status.READY
            if skipped:
                notes.append(
                    f"{len(skipped)} rótulo(s) omitido(s) de {len(items)}: " + "; ".join(skipped)
                )
            if notes:
                document.error_message = " ".join(notes)
            document.save()
        except ValidationError as exc:
            # P.ej. "el rótulo no entra en una hoja A4": un error de
            # generación, no de la solicitud (el Document YA existe) — se
            # deja en FAILED con el mensaje, nunca a medio camino en
            # "processing" ni propagado como un 400 con el documento
            # huérfano.
            document.status = Document.Status.FAILED
            detail = exc.detail
            document.error_message = (
                "; ".join(str(v) for v in detail.values())
                if isinstance(detail, dict)
                else str(detail)
            )
            document.save(update_fields=["status", "error_message", "updated_at"])
        except Exception as exc:  # noqa: BLE001 - un lote no puede quedar a medio camino
            document.status = Document.Status.FAILED
            document.error_message = str(exc)
            document.save(update_fields=["status", "error_message", "updated_at"])
