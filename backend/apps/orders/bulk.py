"""Lógica de las acciones masivas sobre pedidos propios (vistas en
``bulk_views``): exportar a CSV/Excel y cruzar la planilla de seguimientos
del transportista con los pedidos.

Nada de esto conoce una plataforma ni un transportista: la planilla puede
venir de cualquiera, por eso las columnas se reconocen por sinónimos y el
comerciante puede corregir lo que se detectó.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.db.models import Q
from django.utils import timezone
from openpyxl import Workbook

from .models import Order

# Tope de pedidos por acción masiva: cubre un día de despacho holgado y
# evita que una sola llamada recorra miles de pedidos dentro de un request.
MAX_BULK_ORDERS = 500

# ---------------------------------------------------------------------------
# Exportación
# ---------------------------------------------------------------------------

EXPORT_COLUMNS = [
    "Pedido",
    "ID interno",
    "Tienda",
    "Estado",
    "Fecha de alta",
    "Destinatario",
    "Calle",
    "Número",
    "Localidad",
    "Provincia",
    "CP",
    "País",
    "Referencia",
    "Teléfono",
    "Email",
    "Envío elegido",
    "Bultos",
    "Peso (kg)",
    "Productos",
    "Transportista",
    "Seguimiento",
    "URL de seguimiento",
    "Descripción",
]


def order_number(order):
    """El número que reconoce el comerciante: el de su tienda, o el nuestro."""
    return order.external_number or str(order.pk)


def _items_summary(items):
    parts = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("sku") or "").strip()
        if not name:
            continue
        quantity = item.get("quantity")
        parts.append(f"{quantity} x {name}" if quantity not in (None, "") else name)
    return " | ".join(parts)


def format_weight(weight):
    # 2.500 -> "2.5", 10.000 -> "10" (Decimal.normalize daría "1E+1").
    text = f"{weight:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def export_row(order):
    address = order.address
    store = order.store_connection
    created = timezone.localtime(order.created_at).strftime("%Y-%m-%d %H:%M")
    weight = order.total_weight_kg
    return [
        order_number(order),
        order.pk,
        (store.name or store.external_store_id) if store else "Cargado a mano",
        order.get_status_display(),
        created,
        address.recipient_name or order.user.get_full_name() or order.user.email,
        address.street,
        address.number,
        address.city,
        address.state,
        address.postal_code,
        address.country,
        address.reference,
        order.contact_phone,
        order.contact_email,
        order.shipping_option,
        order.package_count,
        "" if weight is None else format_weight(weight),
        _items_summary(order.items),
        order.carrier,
        order.tracking_number,
        order.tracking_url,
        order.description,
    ]


# Un valor que empieza con estos caracteres una planilla lo toma como
# fórmula. Los datos del comprador los escribe un tercero, así que se
# neutralizan (inyección de CSV). Un teléfono "+54 11 ..." es solo números
# y no se toca, para no ensuciarlo con la comilla.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_HARMLESS_NUMBER = re.compile(r"^[+-]?[\d\s().-]+$")


def _safe_cell(value):
    if not isinstance(value, str):
        return value
    if value.startswith(_FORMULA_PREFIXES) and not _HARMLESS_NUMBER.match(value):
        return "'" + value
    return value


def export_csv(orders):
    """CSV con ``;`` y BOM: es lo que Excel en español abre bien con doble
    clic (con ``,`` y sin BOM mete todo en una columna y rompe los acentos)."""
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(EXPORT_COLUMNS)
    for order in orders:
        writer.writerow([_safe_cell(value) for value in export_row(order)])
    return buffer.getvalue().encode("utf-8")


def export_xlsx(orders):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Pedidos"
    sheet.append(EXPORT_COLUMNS)
    for order in orders:
        sheet.append([_safe_cell(value) for value in export_row(order)])
    # Todo texto queda como texto: openpyxl escribe como fórmula cualquier
    # cadena que empiece con "=", aun después de _safe_cell.
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    sheet.freeze_panes = "A2"
    for column_cells in sheet.columns:
        width = max(len(str(cell.value or "")) for cell in column_cells[:200])
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 8), 50)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Planilla de seguimientos del transportista
# ---------------------------------------------------------------------------

TRACKING_FIELDS = ("order", "tracking_number", "carrier", "tracking_url")

# Sinónimos de encabezado, ya normalizados (minúsculas, sin acentos ni
# signos). Se prueba primero la coincidencia exacta y después "contiene".
# El orden importa: seguimiento se busca antes que pedido, porque
# "numero de seguimiento" también contiene "numero".
HEADER_SYNONYMS = {
    "tracking_url": [
        "url de seguimiento", "url seguimiento", "link de seguimiento", "link seguimiento",
        "tracking url", "url", "link", "enlace",
    ],
    "tracking_number": [
        "numero de seguimiento", "nro de seguimiento", "codigo de seguimiento", "seguimiento",
        "tracking number", "tracking", "numero de guia", "nro de guia", "nro guia", "guia",
        "codigo de envio", "numero de envio", "nro de envio",
    ],
    "carrier": ["transportista", "empresa de transporte", "empresa", "correo", "carrier", "courier"],
    "order": [
        "numero de pedido", "nro de pedido", "nro pedido", "n pedido", "pedido", "numero de orden",
        "nro de orden", "orden", "order number", "order", "referencia del pedido", "referencia",
        "ref",
    ],
}


def normalize_header(header):
    text = unicodedata.normalize("NFKD", str(header or "")).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return " ".join(text.split())


def detect_columns(headers):
    """Adivina qué columna es cuál. Devuelve ``{campo: encabezado o None}``;
    una columna se asigna a un solo campo."""
    detected = match_columns(headers, [(field, HEADER_SYNONYMS[field]) for field in ("tracking_url", "tracking_number", "carrier", "order")])
    return {field: detected.get(field) for field in TRACKING_FIELDS}


def match_columns(headers, fields, *, contains_min_words=1):
    """``fields``: ``[(campo, [sinónimos normalizados]), ...]`` en orden de
    prioridad. Devuelve ``{campo: encabezado o None}``; una columna se asigna a
    un solo campo, primero por igualdad y después por "contiene" (por palabras
    enteras, para que "ref" no caiga en "preferencia"). ``contains_min_words``:
    los sinónimos más cortos que eso solo valen por igualdad (que "numero" no
    se lleve "numero de telefono"). La usan el importador de seguimientos y el
    de Empretienda."""
    normalized = {header: normalize_header(header) for header in headers}
    used = set()
    detected = {}
    for field, synonyms in fields:
        match = None
        for synonym in synonyms:
            match = next(
                (h for h in headers if h not in used and normalized[h] == synonym), None
            )
            if match:
                break
        if not match:
            for synonym in synonyms:
                if len(synonym.split()) < contains_min_words:
                    continue
                pattern = re.compile(rf"\b{re.escape(synonym)}\b")
                match = next(
                    (h for h in headers if h not in used and pattern.search(normalized[h])), None
                )
                if match:
                    break
        detected[field] = match
        if match:
            used.add(match)
    return detected


def normalize_order_key(value):
    """"#1001", " 1001 " y el 1001.0 que deja una celda numérica de Excel
    son el mismo pedido."""
    text = str(value or "").strip().lstrip("#").strip()
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    return text


def _order_summary(order):
    address = order.address
    return {
        "id": order.pk,
        "number": order_number(order),
        "recipient": address.recipient_name or "",
        "city": address.city,
        "store_name": order.store_connection.name if order.store_connection else None,
        "status": order.status,
        "status_label": order.get_status_display(),
        "carrier": order.carrier,
        "tracking_number": order.tracking_number,
    }


def _find_orders(user, keys, store=None):
    """``{clave: [pedidos]}`` buscando por número de la tienda, después por
    id externo y por último por nuestro id (el número que muestra la app
    para un pedido cargado a mano). Cada nivel solo se usa si el anterior no
    encontró nada para esa clave."""
    queryset = Order.objects.filter(user=user).select_related("address", "store_connection")
    if store == "manual":
        queryset = queryset.filter(store_connection__isnull=True)
    elif store:
        queryset = queryset.filter(store_connection_id=store)
    digit_keys = [int(key) for key in keys if key.isdigit() and len(key) < 10]
    candidates = list(
        queryset.filter(
            Q(external_number__in=keys) | Q(external_id__in=keys) | Q(pk__in=digit_keys)
        )
    )
    found = {}
    for key in keys:
        for matches in (
            [o for o in candidates if o.external_number == key],
            [o for o in candidates if o.external_id == key],
            [o for o in candidates if key.isdigit() and o.pk == int(key)],
        ):
            if matches:
                found[key] = matches
                break
    return found


_url_validator = URLValidator(schemes=["http", "https"])


def match_tracking_rows(user, rows, mapping, default_carrier="", store=None):
    """Cruza cada fila de la planilla con un pedido propio.

    Devuelve una lista con, por fila, el resultado (``state``): ``ok``,
    ``not_found``, ``ambiguous`` (el número es de más de un pedido: pasa con
    dos tiendas que numeran igual), ``not_shippable`` (cancelado o
    entregado), ``missing_tracking``, ``invalid_url``, ``missing_order`` o
    ``duplicate`` (el mismo pedido ya apareció más arriba). Solo las ``ok``
    se pueden confirmar."""
    order_column = mapping.get("order")
    tracking_column = mapping.get("tracking_number")
    carrier_column = mapping.get("carrier")
    url_column = mapping.get("tracking_url")

    parsed = []
    for index, row in enumerate(rows):
        parsed.append(
            {
                # +2: la fila 1 es el encabezado y las planillas cuentan desde 1.
                "row": index + 2,
                "order_key": normalize_order_key(row.get(order_column)) if order_column else "",
                "tracking_number": str(row.get(tracking_column) or "").strip() if tracking_column else "",
                "carrier": (
                    str(row.get(carrier_column) or "").strip() if carrier_column else ""
                ) or default_carrier,
                "tracking_url": str(row.get(url_column) or "").strip() if url_column else "",
            }
        )

    keys = sorted({item["order_key"] for item in parsed if item["order_key"]})
    found = _find_orders(user, keys, store=store)

    seen_orders = set()
    results = []
    for item in parsed:
        item["order"] = None
        key = item["order_key"]
        matches = found.get(key, []) if key else []
        if not key:
            item["state"], item["message"] = "missing_order", "La fila no tiene número de pedido."
        elif not matches:
            item["state"], item["message"] = "not_found", f"No hay ningún pedido #{key}."
        elif len(matches) > 1:
            stores = ", ".join(
                sorted({o.store_connection.name if o.store_connection else "cargado a mano" for o in matches})
            )
            item["state"] = "ambiguous"
            item["message"] = f"El #{key} existe en más de una tienda ({stores}): elegí la tienda arriba."
        else:
            order = matches[0]
            item["order"] = _order_summary(order)
            if order.pk in seen_orders:
                item["state"], item["message"] = "duplicate", "Este pedido ya aparece más arriba en la planilla."
            elif not order.is_shippable:
                item["state"] = "not_shippable"
                item["message"] = f"El pedido está «{order.get_status_display()}»: no se puede actualizar."
            elif not item["tracking_number"]:
                item["state"], item["message"] = "missing_tracking", "La fila no tiene número de seguimiento."
            elif (
                len(item["tracking_number"]) > 100
                or len(item["carrier"]) > 100
                or len(item["tracking_url"]) > 200
            ):
                item["state"], item["message"] = "invalid", "El seguimiento, el transportista o la URL son demasiado largos."
            else:
                item["state"], item["message"] = "ok", ""
                if item["tracking_url"]:
                    try:
                        _url_validator(item["tracking_url"])
                    except DjangoValidationError:
                        item["state"], item["message"] = "invalid_url", "La URL de seguimiento no es válida."
            seen_orders.add(order.pk)
        results.append(item)
    return results
