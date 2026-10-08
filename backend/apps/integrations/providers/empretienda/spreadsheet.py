"""La planilla de ventas que exporta Empretienda, traducida a pedidos.

Empretienda la genera en "Gestión de ventas → Listado de ventas → Exportar".
Sus encabezados exactos no están publicados en ningún lado: los sinónimos de
``FIELDS`` son los nombres más probables (A CONFIRMAR con un archivo real). Por
eso el flujo tiene dos pasos (``views.py``): primero se muestra qué columna se
tomó para cada dato y cómo quedan los pedidos, el comerciante corrige lo que
haga falta, y recién después se confirma. El mapeo corregido se guarda en la
tienda (``preferences["empretienda_mapping"]``) y se usa la próxima vez.

- **Una fila por producto.** Es lo habitual en estas planillas: las filas de
  un mismo número de orden se juntan en un pedido (``group_orders``).
- **Reimportar no duplica.** Cada pedido se guarda con
  ``upsert_store_order`` por (tienda, número de orden): exportar "las ventas de
  hoy" dos veces, o rangos que se pisan, actualiza en vez de duplicar. El
  estado solo avanza (``_synced_status``): un pedido despachado acá no vuelve
  atrás porque la planilla vieja diga "pendiente".
- Del comprador solo se usa lo que va en el rótulo: aunque la planilla traiga
  email, teléfono o DNI, no se leen.
"""

from __future__ import annotations

from apps.orders.bulk import match_columns, normalize_header, normalize_order_key

from ..addresses import split_street
from ..base import NormalizedOrder
from ..woocommerce.provider import AR_STATES

# Campo -> (etiqueta para el comerciante, obligatorio, sinónimos ya
# normalizados con ``apps.orders.bulk.normalize_header``). El orden importa:
# una columna se asigna al primer campo que la reclama, así que "número de
# orden" se lo lleva la orden antes de que "número" (la altura) lo vea, y los
# "estado de ..." se resuelven antes que un "estado" suelto.
FIELDS = [
    ("order", "Número de orden", True, ["numero de orden", "nro de orden", "n de orden", "n orden", "orden", "numero de venta", "nro de venta", "venta", "id de orden", "id orden", "numero de pedido", "nro de pedido", "pedido"]),
    ("shipping_status", "Estado de envío", False, ["estado de envio", "estado del envio", "envio estado"]),
    ("payment_status", "Estado de pago", False, ["estado de pago", "estado del pago", "pago estado"]),
    ("order_status", "Estado de la orden", False, ["estado de la orden", "estado de orden", "estado de la venta", "estado"]),
    ("recipient", "Destinatario (nombre y apellido)", False, ["nombre y apellido", "nombre completo", "destinatario", "nombre del destinatario", "nombre y apellido del destinatario", "cliente", "comprador"]),
    ("first_name", "Nombre", False, ["nombre", "nombre del cliente"]),
    ("last_name", "Apellido", False, ["apellido", "apellido del cliente"]),
    ("postal_code", "Código postal", False, ["codigo postal", "cp", "c p"]),
    ("street", "Calle", True, ["calle", "direccion", "domicilio", "direccion de envio", "domicilio de envio"]),
    ("number", "Número (altura)", False, ["numero", "altura", "nro", "numero de calle", "numero de domicilio"]),
    ("apartment", "Piso / departamento", False, ["piso", "departamento", "depto", "dpto", "piso y departamento", "piso depto", "piso dpto"]),
    ("city", "Ciudad / localidad", True, ["ciudad", "localidad", "ciudad localidad"]),
    ("state", "Provincia", False, ["provincia"]),
    ("shipping_method", "Método de envío", False, ["metodo de envio", "forma de envio", "tipo de envio", "envio"]),
    ("product", "Producto", False, ["producto", "productos", "nombre del producto", "articulo", "item", "detalle"]),
    ("quantity", "Cantidad", False, ["cantidad", "unidades", "cant"]),
    ("sku", "SKU", False, ["sku", "codigo de producto", "codigo"]),
]
FIELD_LABELS = {field: label for field, label, _required, _synonyms in FIELDS}
REQUIRED_FIELDS = [field for field, _label, required, _synonyms in FIELDS if required]

MAPPING_PREF = "empretienda_mapping"


class SpreadsheetError(Exception):
    """La planilla no se puede usar tal cual; el mensaje es para el comerciante."""


def detect_mapping(headers, saved=None):
    """``{campo: encabezado o None}``. Primero lo que el comerciante corrigió
    la vez anterior (``saved``), si esa columna sigue estando; el resto, por
    sinónimos. Los sinónimos de una palabra ("numero", "envio") solo valen por
    igualdad, para que no se lleven "numero de telefono" o "costo de envio"."""
    saved = {field: header for field, header in (saved or {}).items() if header in headers and field in FIELD_LABELS}
    remaining = [header for header in headers if header not in saved.values()]
    detected = match_columns(
        remaining,
        [(field, synonyms) for field, _label, _required, synonyms in FIELDS if field not in saved],
        contains_min_words=2,
    )
    detected.update(saved)
    return {field: detected.get(field) for field in FIELD_LABELS}


def clean_mapping(mapping, headers):
    """El mapeo que manda el comerciante, solo con campos y columnas que existen."""
    if not isinstance(mapping, dict):
        return {}
    return {field: header for field, header in mapping.items() if field in FIELD_LABELS and header in headers}


def missing_required(mapping):
    """Los campos obligatorios sin columna. El destinatario es obligatorio,
    pero puede venir junto o en nombre + apellido."""
    missing = [FIELD_LABELS[field] for field in REQUIRED_FIELDS if not mapping.get(field)]
    if not mapping.get("recipient") and not mapping.get("first_name"):
        missing.insert(1, FIELD_LABELS["recipient"])
    return missing


def _cell(row, mapping, field):
    header = mapping.get(field)
    return str(row.get(header) or "").strip() if header else ""


def _local_status(values):
    """Estado local según las columnas de estado de la planilla, o "" si no
    dicen nada del envío. A CONFIRMAR: los textos exactos de Empretienda."""
    shipping, payment, order = (normalize_header(value) for value in values)
    if any(word in text for text in (order, payment, shipping) for word in ("cancel", "anulad")):
        return "cancelled"
    if "entregad" in shipping or "entregad" in order:
        return "delivered"
    if any(word in shipping for word in ("en camino", "en transito", "en viaje")):
        return "in_transit"
    if any(word in shipping for word in ("enviad", "despachad")):
        return "dispatched"
    return ""


def _quantity(value):
    text = str(value or "").strip().replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return 1
    if number <= 0:
        return 1
    return int(number) if number.is_integer() else number


def group_orders(rows, mapping):
    """Las filas agrupadas por número de orden, en el orden de la planilla.
    Devuelve ``[(número, [filas])]``; las filas sin número quedan en el número
    vacío, para informarlas como error."""
    groups = {}
    for row in rows:
        key = normalize_order_key(_cell(row, mapping, "order"))
        groups.setdefault(key, []).append(row)
    return list(groups.items())


def build_order(number, rows, mapping):
    """``(NormalizedOrder, errores)`` de las filas de una orden. Con errores,
    el pedido no se guarda (falta a dónde mandarlo)."""
    # La dirección sale de la primera fila que la tenga: en una planilla por
    # producto, las filas siguientes a veces la dejan vacía.
    first = next((row for row in rows if _cell(row, mapping, "street")), rows[0])
    recipient = _cell(first, mapping, "recipient") or " ".join(
        part for part in (_cell(first, mapping, "first_name"), _cell(first, mapping, "last_name")) if part
    )
    street, number_text = _cell(first, mapping, "street"), _cell(first, mapping, "number")
    if not number_text:
        street, number_text = split_street(street)
    city = _cell(first, mapping, "city")
    state = _cell(first, mapping, "state")
    state = AR_STATES.get(state.upper(), state) if len(state) == 1 else state

    errors = []
    if not number:
        errors.append("La fila no tiene número de orden.")
    for value, label in ((recipient, "destinatario"), (street, "calle"), (city, "ciudad")):
        if not value:
            errors.append(f"Falta el dato de {label}.")

    items = []
    for row in rows:
        name = _cell(row, mapping, "product")
        if name:
            items.append({"name": name, "sku": _cell(row, mapping, "sku"), "quantity": _quantity(_cell(row, mapping, "quantity"))})

    status_values = [
        next((_cell(row, mapping, field) for row in rows if _cell(row, mapping, field)), "")
        for field in ("shipping_status", "payment_status", "order_status")
    ]
    normalized = NormalizedOrder(
        external_id=number,
        external_number=number,
        recipient_name=recipient,
        street=street,
        number=number_text,
        city=city,
        state=state,
        postal_code=_cell(first, mapping, "postal_code"),
        country="Argentina",
        reference=_cell(first, mapping, "apartment"),
        description=", ".join(f"{item['quantity']}x {item['name']}" for item in items),
        shipping_option=_cell(first, mapping, "shipping_method"),
        status=_local_status(status_values),
        items=items,
        # Lo que se guarda es lo mismo que va al rótulo: nada de la planilla
        # cruda (puede traer email, teléfono o DNI del comprador).
        raw={"source": "empretienda_spreadsheet", "order": number, "statuses": status_values},
    )
    return normalized, errors
