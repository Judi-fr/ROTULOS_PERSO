"""Traducción de datos ajenos (una fila de planilla, un JSON de API, un
payload de webhook) a un ``Address``+``Order`` propios, y su creación
idempotente por ``external_id``.

Es el ÚNICO camino de creación para toda entrada operativa de pedidos:
carga manual (``views.ManualOrderCreateView``), importación CSV/Excel
(``import_views``), API de ingesta y webhook entrante
(``apps.integrations``) vía ``create_order_from_data``, y pedidos de una
tienda online conectada vía ``upsert_store_order``. El problema es siempre el mismo —"traducir
campos ajenos a los propios" (ver el mapeo) y "no duplicar si ya existe"
(ver ``create_order_from_data``)— así que se resuelve acá una sola vez.
"""

from __future__ import annotations

import re
import unicodedata

from django.db import transaction

from .models import Address, Order

# Campos destino (comunes a carga manual, importación y API/webhook) y si
# son obligatorios. "destinatario"/"domicilio"/"ciudad" son el mínimo para
# que un envío tenga a dónde ir; el resto es opcional.
TARGET_FIELDS = {
    "destinatario": {"label": "Destinatario", "required": True},
    "domicilio": {"label": "Domicilio (calle)", "required": True},
    "numero": {"label": "Número", "required": False},
    "ciudad": {"label": "Ciudad", "required": True},
    "provincia": {"label": "Provincia", "required": False},
    "cp": {"label": "Código postal", "required": False},
    "referencia": {"label": "Referencia", "required": False},
    "descripcion": {"label": "Descripción", "required": False},
    "external_id": {"label": "ID externo", "required": False},
}

# Encabezados conocidos por campo destino, ya normalizados (ver
# normalize_header): auto-detección de mapeo para que una planilla con
# nombres de columna razonables se mapee sola (story 22).
AUTO_DETECT_ALIASES = {
    "destinatario": {"destinatario", "nombre", "cliente", "receptor", "nombreapellido"},
    "domicilio": {"domicilio", "direccion", "calle"},
    "numero": {"numero", "nro", "altura", "numerodomicilio"},
    "ciudad": {"ciudad", "localidad"},
    "provincia": {"provincia", "estado"},
    "cp": {"cp", "codigopostal"},
    "referencia": {"referencia", "observaciones", "obs"},
    "descripcion": {"descripcion", "detalle", "producto", "contenido"},
    "external_id": {"idexterno", "externalid", "id", "codigo", "numeropedido", "nropedido"},
}


def normalize_header(value):
    """"Código Postal ", "código_postal", "CODIGO POSTAL" -> "codigopostal":
    minúscula, sin acentos ni espacios/separadores, para que la
    auto-detección no dependa de cómo cada planilla decidió escribirlo."""
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", text.lower())


def auto_detect_mapping(headers):
    """``{campo_destino: encabezado_original}`` para cada encabezado que
    coincide con un alias conocido. Un encabezado no reconocido queda sin
    mapear — el usuario lo completa a mano en la pantalla de mapeo."""
    mapping = {}
    for header in headers:
        normalized = normalize_header(header)
        for target, aliases in AUTO_DETECT_ALIASES.items():
            if target in mapping:
                continue
            if normalized in aliases:
                mapping[target] = header
                break
    return mapping


def apply_mapping(row, mapping):
    """``row``: dict encabezado/clave -> valor crudo. ``mapping``: dict
    campo_destino -> encabezado/clave de ``row``. Devuelve los valores ya
    en los campos destino, como texto recortado (o ``None`` si faltan)."""
    data = {}
    for target, source_key in (mapping or {}).items():
        if not source_key:
            continue
        value = row.get(source_key)
        if isinstance(value, str):
            value = value.strip()
        elif value is not None:
            value = str(value).strip()
        data[target] = value or None
    return data


def validate_mapped_row(data):
    """Lista de mensajes de error (vacía si la fila está OK) — nunca
    lanza: el llamador decide qué hacer con la fila (saltearla, contarla),
    la misma fila rota no puede tumbar el resto de la importación/lote."""
    errors = []
    for field, meta in TARGET_FIELDS.items():
        if meta["required"] and not data.get(field):
            errors.append(f"Falta '{meta['label']}'.")
    return errors


@transaction.atomic
def create_order_from_data(user, data, *, source):
    """Crea (o recupera) el ``Order`` a partir de datos ya mapeados a
    ``TARGET_FIELDS``. Idempotente por ``external_id``: si ``user`` ya
    tiene un pedido con ese ``external_id``, lo devuelve tal cual
    (``created=False``) en vez de crear uno nuevo — así reimportar el
    mismo archivo, o reintentar la misma llamada de API/webhook, nunca
    duplica.

    ``data`` debe haber pasado ``validate_mapped_row`` sin errores: acá no
    se revalida, solo se asume ya válido (separar valida/crea es lo que le
    permite a la importación reportar TODAS las filas rotas de una vez, en
    vez de una por una).
    """
    external_id = (data.get("external_id") or "").strip() or None
    if external_id:
        # Solo contra pedidos que NO vienen de una tienda conectada: esos
        # tienen su propia idempotencia por tienda (upsert_store_order).
        existing = Order.objects.filter(
            user=user, external_id=external_id, store_connection__isnull=True
        ).first()
        if existing is not None:
            return existing, False

    address = Address.objects.create(
        user=user,
        # Dirección de un comprador, no de la agenda del usuario: no se lista
        # en "Mis direcciones" (ver Address.Origin).
        origin=Address.Origin.SHIPMENT,
        recipient_name=data.get("destinatario") or "",
        street=data.get("domicilio") or "",
        number=data.get("numero") or "",
        city=data.get("ciudad") or "",
        state=data.get("provincia") or "",
        postal_code=data.get("cp") or "",
        reference=data.get("referencia") or "",
    )
    order = Order.objects.create(
        user=user,
        address=address,
        description=data.get("descripcion") or "",
        external_id=external_id,
        source=source,
    )
    return order, True


def _fit(model, field_name, value):
    """Recorta ``value`` al ``max_length`` del campo: un dato largo de una
    tienda no puede hacer fallar el alta (Postgres sí aplica el límite)."""
    value = "" if value is None else str(value)
    max_length = model._meta.get_field(field_name).max_length
    return value[:max_length] if max_length else value


@transaction.atomic
def upsert_store_order(connection, normalized):
    """Crea o actualiza el pedido de una tienda conectada a partir de un
    ``apps.integrations.providers.NormalizedOrder``. Devuelve
    ``(order, result)`` con ``result`` en ``"created"``, ``"updated"`` o
    ``"unchanged"``.

    Idempotente por ``(connection, external_id)``: el mismo aviso repetido
    no duplica. Si el pedido ya existe y el dato recibido no es más nuevo
    que el guardado (``external_updated_at``), no se toca: los webhooks
    pueden llegar desordenados.

    Estado: se sincroniza con lo que diga la tienda (``normalized.status``)
    solo hacia adelante (ver ``_synced_status``) y sin avisarle de vuelta a
    la tienda, que es quien lo informó.
    """
    if connection.owner_id is None:
        # Instalada pero todavía no vinculada a una cuenta (ver
        # apps.integrations.stores.claim_store): no hay a nombre de quién
        # crear el pedido.
        raise ValueError("La tienda todavía no está vinculada a una cuenta.")

    address_values = {
        "recipient_name": _fit(Address, "recipient_name", normalized.recipient_name),
        "street": _fit(Address, "street", normalized.street),
        "number": _fit(Address, "number", normalized.number),
        "city": _fit(Address, "city", normalized.city),
        "state": _fit(Address, "state", normalized.state),
        "postal_code": _fit(Address, "postal_code", normalized.postal_code),
        "country": _fit(Address, "country", normalized.country or "Argentina"),
        "reference": _fit(Address, "reference", normalized.reference),
    }
    order_values = {
        "description": _fit(Order, "description", normalized.description),
        "external_number": _fit(Order, "external_number", normalized.external_number),
        "contact_email": _fit(Order, "contact_email", normalized.contact_email),
        "contact_phone": _fit(Order, "contact_phone", normalized.contact_phone),
        "shipping_option": _fit(Order, "shipping_option", normalized.shipping_option),
        "package_count": max(int(normalized.package_count or 1), 1),
        "total_weight_kg": normalized.total_weight_kg,
        "items": normalized.items or [],
        "raw_payload": normalized.raw or {},
        "external_updated_at": normalized.external_updated_at,
    }
    external_id = _fit(Order, "external_id", normalized.external_id)

    order = (
        Order.objects.select_for_update()
        .select_related("address")
        .filter(store_connection=connection, external_id=external_id)
        .first()
    )
    if order is None:
        address = Address.objects.create(
            user=connection.owner, origin=Address.Origin.SHIPMENT, **address_values
        )
        order = Order.objects.create(
            user=connection.owner,
            address=address,
            store_connection=connection,
            external_id=external_id,
            source=Order.Source.STORE,
            status=_synced_status(Order.Status.CREATED, normalized.status) or Order.Status.CREATED,
            **order_values,
        )
        return order, "created"

    incoming = normalized.external_updated_at
    if incoming is not None and order.external_updated_at is not None and incoming <= order.external_updated_at:
        return order, "unchanged"

    for field_name, value in address_values.items():
        setattr(order.address, field_name, value)
    order.address.save()
    for field_name, value in order_values.items():
        setattr(order, field_name, value)
    new_status = _synced_status(order.status, normalized.status)
    if new_status:
        order.status = new_status
        # Lo informó la tienda: no hace falta avisarle de vuelta.
        order._skip_store_notification = True
    order.save()
    return order, "updated"


def _synced_status(current, incoming):
    """Estado a aplicar según la tienda, o ``None`` si no corresponde
    cambiarlo: una cancelación se aplica siempre (salvo que ya esté
    cancelado); un estado de envío solo si avanza respecto del actual; un
    pedido cancelado localmente no se reactiva."""
    if not incoming or incoming == current or current == Order.Status.CANCELLED:
        return None
    if incoming == Order.Status.CANCELLED:
        return incoming
    if incoming in Order.SHIPPING_STATUSES and Order.status_rank(incoming) > Order.status_rank(current):
        return incoming
    return None
