"""Envíos por Andreani: de un pedido nuestro a una orden de envío de Andreani, y
de los movimientos de Andreani al estado del pedido.

- **Crear** (``create_shipment``): arma la orden con el remitente y origen de la
  cuenta del cliente, el destinatario y domicilio del pedido (o una sucursal /
  punto HOP), y un bulto con el peso del pedido o el de la cuenta. El número de
  envío que devuelve Andreani queda como seguimiento del pedido: es lo que
  imprime el rótulo debajo del código de barras (``{{tracking}}``, ver
  ``apps.labels.label_rendering``). El pedido pasa a "en preparación", no a
  despachado: recién está despachado cuando Andreani lo admite (lo dice el
  seguimiento), y ahí se le informa a la tienda.
- **Cotizar** (``quote_order``): cuánto cobra Andreani por ese pedido con ese
  contrato (``GET /v1/tarifas``, con el código de cliente de la cuenta). Se
  muestra antes de crear y, al crear, lo cotizado queda en el envío
  (``quoted_price``): si la cotización falla el envío se crea igual.
- **Seguir** (``sync_shipment``): lee las trazas, guarda los movimientos nuevos y
  mueve el envío y el pedido SOLO hacia adelante.
- Del comprador va a Andreani lo necesario para entregar: nombre, domicilio y,
  si el pedido los tiene, email y teléfono (Andreani avisa al destinatario).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.orders.models import Order
from apps.orders.shipping import apply_shipping

from ..models import CarrierAccount, CarrierShipment, CarrierShipmentEvent
from .client import AndreaniClient, AndreaniError

logger = logging.getLogger(__name__)

CARRIER_NAME = "Andreani"
ANDREANI_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

# Andreani limita varios campos a 40 caracteres.
MAX_TEXT = 40

# --- Movimientos de Andreani -> estados ------------------------------------
#
# Del "Maestro de eventos y estados" de Andreani. El ciclo importa: un
# "EnvioEntregado" en el ciclo de devolución (Drop/Devolucion) es el paquete
# volviendo al remitente, no al comprador.
FORWARD_CYCLES = {"distribution", "directo", "resend", "reenvio"}
RETURN_CYCLES = {"drop", "devolucion", "rescate", "logistica inversa"}
ADMITTED_EVENTS = {"admision", "altaautomatica"}
MOVING_EVENTS = {
    "envioconsolidado",
    "enviodespachado",
    "expedicionhojaderutacabecera",
    "expedicionhojaderutadeviaje",
    "recepcionensucursaldestino",
    "distribucion",
    "visita",
    "reenvio",
}
AT_BRANCH_EVENTS = {"comienzocustodiaensucursal"}
ISSUE_EVENTS = {"envionoentregado", "siniestro", "roturatotal", "cierredeentidad"}

STATUS_RANK = {
    CarrierShipment.Status.PENDING: 0,
    CarrierShipment.Status.IN_TRANSIT: 1,
    CarrierShipment.Status.AT_BRANCH: 2,
    CarrierShipment.Status.ISSUE: 3,
    CarrierShipment.Status.RETURNING: 4,
    CarrierShipment.Status.DELIVERED: 5,
    CarrierShipment.Status.CANCELLED: 6,
}


class ShipmentError(Exception):
    """El envío no se puede crear o actualizar; el mensaje es para el comerciante."""


def _text(value, limit=MAX_TEXT):
    return str(value or "").strip()[:limit]


def _key(value):
    return re.sub(r"[^a-z]", "", str(value or "").lower())


def postal_code_digits(value):
    """Los 4 dígitos del CP argentino (un CPA ``C1602ABC`` da ``1602``)."""
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:4]


def tracking_url_for(number):
    template = str(getattr(settings, "ANDREANI_TRACKING_URL_TEMPLATE", "https://www.andreani.com/envio/{numero}"))
    return template.format(numero=number)


# --- Alta -------------------------------------------------------------------


def account_problems(account):
    """Lo que le falta a la cuenta para poder crear envíos (lista de textos)."""
    problems = []
    if not account.username or not account.password_encrypted:
        problems.append("el usuario y la contraseña de Andreani")
    if not account.contracts:
        problems.append("al menos un contrato")
    if not account.sender_name:
        problems.append("el nombre del remitente")
    if not (account.origin_street and account.origin_number and account.origin_city and postal_code_digits(account.origin_postal_code)):
        problems.append("el domicilio de origen (calle, número, localidad y código postal)")
    return problems


def _weight(order, account):
    try:
        weight = Decimal(order.total_weight_kg) if order.total_weight_kg else Decimal(account.default_weight_kg)
    except (InvalidOperation, TypeError):
        weight = Decimal(account.default_weight_kg)
    return max(weight, Decimal("0.001"))


def _packages(order, account, package_count):
    """Los bultos del pedido: el peso repartido en partes iguales y el volumen
    por defecto de la cuenta en cada uno."""
    count = max(int(package_count or 1), 1)
    weight = round(_weight(order, account) / count, 3)
    return count, weight


def _decimal(value):
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return None


def quote_order(account, order, contract_code, *, package_count=1, client=None):
    """Lo que cobra Andreani por ``order`` con el contrato ``contract_code``:
    ``{"price", "price_without_tax", "insurance", "chargeable_weight_kg"}``
    (``Decimal``; ``price`` es con IVA, lo que paga el cliente). Se cotiza al
    código postal del pedido también para los contratos de sucursal: la tarifa
    es por zona de destino. ``ShipmentError`` con el motivo si no se puede."""
    if not account.username or not account.password_encrypted:
        raise ShipmentError("A tu cuenta de Andreani le falta el usuario y la contraseña.")
    if not account.client_code:
        raise ShipmentError("Para cotizar, a tu cuenta de Andreani le falta el código de cliente.")
    contract = account.contract(contract_code)
    if contract is None:
        raise ShipmentError("Ese contrato no está en tu cuenta de Andreani.")
    postal_code = postal_code_digits(order.address.postal_code if order.address_id else "")
    if not postal_code:
        raise ShipmentError("El pedido no tiene código postal: Andreani cotiza por destino.")
    count, weight = _packages(order, account, package_count)
    packages = [{"volumen": int(account.default_volume_cm3), "kilos": float(weight)} for _index in range(count)]
    try:
        data = (client or AndreaniClient(account)).quote(
            contract=contract["code"], client_code=account.client_code, postal_code=postal_code, packages=packages
        )
    except AndreaniError as exc:
        raise ShipmentError(str(exc)) from exc
    with_tax = data.get("tarifaConIva") or {}
    without_tax = data.get("tarifaSinIva") or {}
    price = _decimal(with_tax.get("total"))
    if price is None:
        raise ShipmentError("Andreani no devolvió el precio del envío.")
    return {
        "price": price.quantize(Decimal("0.01")),
        "price_without_tax": _decimal(without_tax.get("total")),
        "insurance": _decimal(with_tax.get("seguroDistribucion")),
        "chargeable_weight_kg": _decimal(data.get("pesoAforado")),
    }


def build_order_payload(account, order, contract, *, branch_id="", package_count=1):
    """La orden de envío (``POST /v2/ordenes-de-envio``) para ``order``."""
    address = order.address
    reference = _text(order.external_number or order.pk, 30)
    origin = {
        "postal": {
            "codigoPostal": postal_code_digits(account.origin_postal_code),
            "calle": _text(account.origin_street),
            "numero": _text(account.origin_number),
            "localidad": _text(account.origin_city),
            "pais": "Argentina",
        }
    }
    if account.origin_floor:
        origin["postal"]["piso"] = _text(account.origin_floor)
    if account.origin_apartment:
        origin["postal"]["departamento"] = _text(account.origin_apartment)

    if branch_id:
        destination = {"sucursal": {"id": str(branch_id)}}
    else:
        postal = {
            "codigoPostal": postal_code_digits(address.postal_code),
            "calle": _text(address.street),
            "numero": _text(address.number) or "S/N",
            "localidad": _text(address.city),
            "pais": "Argentina",
        }
        if address.reference:
            # Piso, depto, entre calles: Andreani los lleva como componentes.
            postal["componentesDeDireccion"] = [{"meta": "observaciones", "contenido": _text(address.reference, 100)}]
        destination = {"postal": postal}

    sender = {"nombreCompleto": _text(account.sender_name)}
    if account.sender_email:
        sender["email"] = account.sender_email
    if account.sender_document:
        sender["documentoTipo"] = "CUIT"
        sender["documentoNumero"] = _text(account.sender_document, 20)
    if account.sender_phone:
        sender["telefonos"] = [{"tipo": 1, "numero": _text(account.sender_phone, 15)}]

    recipient = {"nombreCompleto": _text(address.recipient_name)}
    if order.contact_email:
        recipient["email"] = _text(order.contact_email)
    if order.contact_phone:
        recipient["telefonos"] = [{"tipo": 2, "numero": _text(order.contact_phone, 15)}]

    count, weight = _packages(order, account, package_count)
    packages = []
    for index in range(count):
        package = {
            "kilos": float(weight),
            "volumenCm": int(account.default_volume_cm3),
            # B2C: la referencia del cliente va como "idCliente" del bulto y
            # Andreani la muestra en su seguimiento.
            "referencias": [{"meta": "idCliente", "contenido": reference}],
            "descripcion": _text(f"Pedido {reference}" + (f" ({index + 1}/{count})" if count > 1 else "")),
        }
        packages.append(package)

    return {
        "contrato": str(contract),
        "idPedido": reference,
        "origen": origin,
        "destino": destination,
        "remitente": sender,
        "destinatario": [recipient],
        "bultos": packages,
    }


def create_shipment(request, account, order, contract_code, *, branch_id="", branch_name="", package_count=1):
    """Crea la orden en Andreani y la deja asociada al pedido. Devuelve el
    ``CarrierShipment``. ``ShipmentError`` con el motivo si no se puede."""
    problems = account_problems(account)
    if problems:
        raise ShipmentError("A tu cuenta de Andreani le falta " + ", ".join(problems) + ".")
    contract = account.contract(contract_code)
    if contract is None:
        raise ShipmentError("Ese contrato no está en tu cuenta de Andreani.")
    if contract.get("kind") == "branch" and not branch_id:
        raise ShipmentError("Para entregar en sucursal hay que elegir la sucursal o el punto HOP.")
    if order.status in (Order.Status.CANCELLED, Order.Status.DELIVERED):
        raise ShipmentError("El pedido está cancelado o ya fue entregado.")
    open_shipment = order.carrier_shipments.exclude(status=CarrierShipment.Status.CANCELLED).first()
    if open_shipment is not None:
        raise ShipmentError(f"El pedido ya tiene el envío de Andreani {open_shipment.tracking_number}.")
    address = order.address
    if not branch_id and not (address.street and address.city and postal_code_digits(address.postal_code)):
        raise ShipmentError("El pedido no tiene calle, localidad y código postal: Andreani los necesita para entregar.")
    if not address.recipient_name:
        raise ShipmentError("El pedido no tiene destinatario.")

    payload = build_order_payload(account, order, contract["code"], branch_id=branch_id, package_count=package_count)
    client = AndreaniClient(account)
    try:
        quoted_price = quote_order(account, order, contract["code"], package_count=package_count, client=client)["price"]
    except ShipmentError as exc:
        # Sin precio el envío igual sale: la cotización es para que el cliente
        # sepa cuánto le cuesta, no una condición para despachar.
        logger.info("No se pudo cotizar el pedido %s en Andreani: %s", order.pk, exc)
        quoted_price = None
    try:
        response = client.create_order(payload)
    except AndreaniError as exc:
        raise ShipmentError(str(exc)) from exc

    packages = response.get("bultos") or []
    number = str(packages[0].get("numeroDeEnvio") or "")
    if not number:
        raise ShipmentError("Andreani creó la orden pero no devolvió el número de envío.")
    with transaction.atomic():
        shipment = CarrierShipment.objects.create(
            account=account,
            order=order,
            carrier=CarrierAccount.Carrier.ANDREANI,
            contract=str(contract["code"]),
            delivery_kind=CarrierShipment.DeliveryKind.BRANCH if branch_id else CarrierShipment.DeliveryKind.HOME,
            branch_id=str(branch_id or ""),
            branch_name=_text(branch_name, 150),
            tracking_number=number,
            group_number=str(response.get("agrupadorDeBultos") or ""),
            package_count=len(packages) or 1,
            carrier_status=_text(response.get("estado"), 120),
            quoted_price=quoted_price,
            raw_response=response,
        )
        target = order.status
        if Order.status_rank(Order.Status.PREPARING) > Order.status_rank(order.status):
            target = Order.Status.PREPARING
        apply_shipping(
            request,
            order,
            {"status": target, "carrier": CARRIER_NAME, "tracking_number": number, "tracking_url": tracking_url_for(number)},
        )
    return shipment


# --- Seguimiento -------------------------------------------------------------


def _parse_moment(value):
    """``2025-10-08T12:19:04.054`` (hora argentina, sin zona) -> aware."""
    text = str(value or "").strip()
    moment = parse_datetime(text) if text else None
    if moment is None:
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
    if timezone.is_naive(moment):
        moment = moment.replace(tzinfo=ANDREANI_TZ)
    return moment


def status_for_event(event):
    """El estado del envío que corresponde a un movimiento, o ``None`` si el
    movimiento no dice nada nuevo (una impresión, una gestión telefónica)."""
    name = _key(event.get("Evento"))
    cycle = str(event.get("Ciclo") or "").strip().lower()
    if name == "envioanulado":
        return CarrierShipment.Status.CANCELLED
    if cycle in RETURN_CYCLES:
        return CarrierShipment.Status.RETURNING
    if name == "envioentregado":
        return CarrierShipment.Status.DELIVERED if cycle in FORWARD_CYCLES or not cycle else CarrierShipment.Status.RETURNING
    if name in ISSUE_EVENTS:
        return CarrierShipment.Status.ISSUE
    if name in AT_BRANCH_EVENTS:
        return CarrierShipment.Status.AT_BRANCH
    if name in ADMITTED_EVENTS or name in MOVING_EVENTS:
        return CarrierShipment.Status.IN_TRANSIT
    return None


def order_status_for(parsed):
    """El estado al que llevan al PEDIDO los movimientos (el más avanzado), o
    ``None``. Admitido por Andreani = despachado (se lo entregaron al correo);
    moviéndose o esperando retiro = en tránsito; entregado al comprador =
    entregado. La devolución no mueve el pedido: queda a la vista en el envío."""
    target = None
    for _moment, event in parsed:
        name = _key(event.get("Evento"))
        cycle = str(event.get("Ciclo") or "").strip().lower()
        if cycle in RETURN_CYCLES:
            continue
        if name == "envioentregado":
            candidate = Order.Status.DELIVERED
        elif name in MOVING_EVENTS or name in AT_BRANCH_EVENTS:
            candidate = Order.Status.IN_TRANSIT
        elif name in ADMITTED_EVENTS:
            candidate = Order.Status.DISPATCHED
        else:
            continue
        if target is None or Order.status_rank(candidate) > Order.status_rank(target):
            target = candidate
    return target


def _store_events(shipment, events):
    """Guarda los movimientos nuevos y devuelve los válidos, ordenados."""
    parsed = []
    for event in events:
        moment = _parse_moment(event.get("Fecha"))
        if moment is None:
            continue
        parsed.append((moment, event))
        CarrierShipmentEvent.objects.get_or_create(
            shipment=shipment,
            occurred_at=moment,
            event=_text(event.get("Evento"), 80),
            defaults={
                "cycle": _text(event.get("Ciclo"), 50),
                "reason": _text(event.get("Motivo"), 150),
                "sub_reason": _text(event.get("Submotivo"), 150),
                "status_text": _text(event.get("Estado"), 150),
                "branch": _text(event.get("Sucursal"), 150),
                "comment": _text(event.get("Comentario"), 500),
            },
        )
    parsed.sort(key=lambda item: item[0])
    return parsed


def sync_shipment(shipment, *, client=None):
    """Lee las trazas en Andreani y actualiza envío y pedido. Devuelve si el
    estado del envío cambió. Los errores de Andreani se propagan (el llamador
    decide si los anota y sigue)."""
    events = (client or AndreaniClient(shipment.account)).traces(shipment.tracking_number)
    parsed = _store_events(shipment, events)
    now = timezone.now()
    new_status = shipment.status
    for _moment, event in parsed:
        candidate = status_for_event(event)
        if candidate is None:
            continue
        # La devolución y la cancelación pisan a todo; lo demás solo avanza.
        if candidate in (CarrierShipment.Status.RETURNING, CarrierShipment.Status.CANCELLED) or (
            STATUS_RANK[candidate] > STATUS_RANK[new_status] and new_status not in CarrierShipment.FINAL_STATUSES
        ):
            new_status = candidate
    changed = new_status != shipment.status
    fields = {"last_checked_at": now, "last_error": ""}
    if parsed:
        last = parsed[-1][1]
        fields["carrier_status"] = _text(last.get("Estado") or last.get("Motivo") or last.get("Evento"), 120)
        fields["last_event_at"] = parsed[-1][0]
    if changed:
        fields["status"] = new_status
    for name, value in fields.items():
        setattr(shipment, name, value)
    shipment.save(update_fields=[*fields, "updated_at"])

    order = shipment.order
    target = order_status_for(parsed)
    if (
        target
        and order.tracking_number == shipment.tracking_number
        and order.status != Order.Status.CANCELLED
        and Order.status_rank(target) > Order.status_rank(order.status)
    ):
        apply_shipping(None, order, {"status": target})
    return changed


def due_shipments(now=None, limit=50):
    """Envíos abiertos que no se consultaron hace ``ANDREANI_TRACKING_POLL_MINUTES``."""
    now = now or timezone.now()
    since = now - timedelta(minutes=getattr(settings, "ANDREANI_TRACKING_POLL_MINUTES", 30))
    # Un envío que pasó dos meses sin cerrarse ya no se sigue solo.
    oldest = now - timedelta(days=getattr(settings, "ANDREANI_TRACKING_MAX_DAYS", 60))
    return (
        CarrierShipment.objects.filter(carrier=CarrierAccount.Carrier.ANDREANI, created_at__gte=oldest, account__is_active=True)
        .exclude(status__in=CarrierShipment.FINAL_STATUSES)
        .filter(Q(last_checked_at__isnull=True) | Q(last_checked_at__lt=since))
        .select_related("account", "order")
        .order_by("last_checked_at")[:limit]
    )


def cancel_shipment(request, shipment):
    """Pide la cancelación a Andreani y, si la acepta, deja el envío cancelado y
    le saca el seguimiento al pedido (que vuelve a poder enviarse). A CONFIRMAR:
    Andreani contesta "ejecutada correctamente" al recibir el pedido; si después
    la rechazara, el seguimiento la vería (``GET /v2/nueva-accion/{numero}``)."""
    if shipment.is_final:
        raise ShipmentError("El envío ya está entregado o cancelado.")
    try:
        AndreaniClient(shipment.account).cancel(shipment.contract, shipment.tracking_number)
    except AndreaniError as exc:
        raise ShipmentError(str(exc)) from exc
    shipment.status = CarrierShipment.Status.CANCELLED
    shipment.carrier_status = "Cancelación solicitada"
    shipment.save(update_fields=["status", "carrier_status", "updated_at"])
    order = shipment.order
    if order.tracking_number == shipment.tracking_number:
        apply_shipping(request, order, {"status": order.status, "carrier": "", "tracking_number": "", "tracking_url": ""})
    return shipment
