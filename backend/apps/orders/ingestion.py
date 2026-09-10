"""Traducción de datos ajenos (una fila de planilla, un JSON de API, un
payload de webhook) a un ``Address``+``Order`` propios, y su creación
idempotente por ``external_id``.

Es el ÚNICO camino de creación para toda entrada operativa de pedidos:
carga manual (``views.ManualOrderCreateView``), importación CSV/Excel
(``import_views``), API de ingesta y webhook entrante
(``apps.integrations``). El problema es siempre el mismo —"traducir
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
        existing = Order.objects.filter(user=user, external_id=external_id).first()
        if existing is not None:
            return existing, False

    address = Address.objects.create(
        user=user,
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
