"""OCA e-Pak en la capa común de transportistas.

- **La cuenta:** usuario y clave de e-Pak (dan de alta y anulan), número de
  cuenta (``nrocuenta``) y el CUIT del remitente (cotizar lo pide). Los
  "contratos" son las **operativas** de OCA: cada una es un servicio
  (puerta a puerta, puerta a sucursal...). "Probar conexión" las trae solas
  (``GetOperativasByUsuario``), con si entregan en sucursal y si salen de una.
- **El alta** es una orden de retiro (``IngresoORMultiplesRetiros_v2``,
  confirmada): un XML con el origen (o la sucursal donde se deja, para las
  operativas "desde sucursal"), el destinatario y los paquetes. Devuelve la
  orden de retiro (``group_number``: con ella se anula y se piden las
  etiquetas) y el número de envío de 19 dígitos (el seguimiento).
- **Estados:** OCA no publica la tabla de ``IdEstado``. Se traduce por el texto
  del estado; confirmados en QA: 1 "En proceso de Retiro", 54 "Envío Cancelado".
  Lo demás es A CONFIRMAR con envíos reales.
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.orders.models import Order

from .. import common
from ..base import AccountField, CarrierEvent, CarrierProvider, CreatedShipment, ShipmentError
from ..models import CarrierShipment
from .client import OcaClient, OcaError

OCA_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
CANCELLED_STATE_IDS = {"54"}
PICKUP_STATE_IDS = {"1"}


def _plain(value):
    return unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().lower()


def fit(value, limit):
    """El texto recortado a ``limit`` BYTES en UTF-8: OCA guarda los campos en
    columnas de ese largo en bytes, y una tilde ocupa dos (visto en QA: una
    provincia de 30 caracteres con "ó" falla con "Error al actualizar
    @TabEnvios")."""
    text = str(value or "").strip()
    while len(text.encode("utf-8")) > limit:
        text = text[:-1]
    return text


def oca_province(state, postal_code=""):
    """La provincia como la escribe OCA en su ejemplo: sin tildes y en
    mayúsculas, CABA como "CAPITAL FEDERAL". Con tilde falla el alta en QA
    ("Error al actualizar @TabEnvios", 2026-10-14), aunque en los nombres pasa."""
    code = common.province_code(state, postal_code)
    if code == "C":
        return "CAPITAL FEDERAL"
    name = common.province_name(code) or str(state or "")
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper().strip()


def format_cuit(value):
    """``30536259194`` -> ``30-53625919-4`` (OCA lo pide con guiones)."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 11:
        return str(value or "").strip()
    return f"{digits[:2]}-{digits[2:10]}-{digits[10]}"


def next_business_day(today=None):
    day = (today or timezone.localdate()) + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def split_name(full_name):
    """``"María José Pérez"`` -> (``"María José"``, ``"Pérez"``): OCA pide
    nombre y apellido por separado."""
    parts = str(full_name or "").split()
    if len(parts) < 2:
        return (parts[0] if parts else "", parts[0] if parts else "")
    return " ".join(parts[:-1]), parts[-1]


def classify(state_id, text):
    """``(estado del envío, estado del pedido)`` de un movimiento de OCA."""
    plain = _plain(text)
    if str(state_id) in CANCELLED_STATE_IDS or "cancelad" in plain or "anulad" in plain:
        return CarrierShipment.Status.CANCELLED, None
    if "devuel" in plain or "devoluc" in plain or "retorno" in plain:
        return CarrierShipment.Status.RETURNING, None
    if "no entregad" in plain or "siniestr" in plain or "extravi" in plain or "rechaz" in plain:
        return CarrierShipment.Status.ISSUE, None
    if "entregad" in plain:
        return CarrierShipment.Status.DELIVERED, Order.Status.DELIVERED
    if "para retirar" in plain or "disponible para retir" in plain or "espera de retiro" in plain:
        return CarrierShipment.Status.AT_BRANCH, Order.Status.IN_TRANSIT
    if str(state_id) in PICKUP_STATE_IDS or "proceso de retiro" in plain:
        # Todavía no lo tiene OCA: la orden de retiro está creada.
        return None, None
    if "distribuc" in plain or "viaje" in plain or "transito" in plain or "camino" in plain or "destino" in plain:
        return CarrierShipment.Status.IN_TRANSIT, Order.Status.IN_TRANSIT
    # Retirado, admitido, ingresado, en proceso en OCA: ya lo tiene OCA.
    return CarrierShipment.Status.IN_TRANSIT, Order.Status.DISPATCHED


def _moment(value):
    text = str(value or "").strip()
    moment = parse_datetime(text.replace(" ", "T", 1)) if text else None
    if moment is None:
        return None
    if timezone.is_naive(moment):
        moment = moment.replace(tzinfo=OCA_TZ)
    return moment


class OcaProvider(CarrierProvider):
    code = "oca"
    name = "OCA"
    account_help = (
        "Se usa tu usuario de e-Pak (el mismo con el que entrás a OCA e-Pak) y tu número de cuenta. "
        "Al probar la conexión se cargan solas tus operativas."
    )
    account_fields = (
        AccountField("username", "Usuario de e-Pak", storage="column", required=True, help="El email con el que entrás a e-Pak."),
        AccountField("password", "Clave de e-Pak", storage="password", required=True),
        AccountField("client_code", "Número de cuenta", storage="column", required=True, max_length=50, help="Por ejemplo 111757/001."),
        AccountField(
            "cost_center",
            "Centro de costo",
            max_length=10,
            help="El número que OCA le asignó al domicilio de retiro. Si no sabés cuál es, dejalo en 0.",
        ),
        AccountField(
            "pickup_slot",
            "Franja de retiro",
            max_length=1,
            choices=(("1", "8 a 17 h"), ("2", "8 a 12 h"), ("3", "14 a 17 h")),
        ),
        AccountField(
            "origin_branch_id",
            "Sucursal donde dejás los paquetes",
            max_length=10,
            help="Solo para operativas “desde sucursal”: el número de centro de imposición de OCA.",
        ),
    )
    contract_label = "Operativa"
    contract_code_label = "Número de operativa"
    contract_one = "una operativa"
    contract_help = "Cada operativa es un servicio de OCA (puerta a puerta, puerta a sucursal...). Al probar la conexión se cargan solas."
    contracts_from_test = True
    label_formats = ("pdf", "zpl")
    label_name = "etiqueta de OCA"
    supports_checkout = True
    checkout_default_name = "OCA a domicilio"

    def tracking_url(self, number):
        template = str(getattr(settings, "OCA_TRACKING_URL_TEMPLATE", "https://www1.oca.com.ar/OEPTrackingWeb/detalleenviore.asp?numero={numero}"))
        return template.format(numero=number)

    def account_problems(self, account):
        problems = super().account_problems(account)
        if any(contract.get("origin") == "branch" for contract in account.contracts or []) and not (account.extra or {}).get("origin_branch_id"):
            problems.append("la sucursal donde dejás los paquetes (tenés operativas desde sucursal)")
        return problems

    def test_credentials(self, account):
        rows = OcaClient(account).operativas()
        contracts = [
            {
                "code": str(row.get("IdOperativa") or ""),
                "label": str(row.get("Descripcion") or "")[:80],
                "kind": "branch" if str(row.get("ASucursal")).lower() == "true" else "home",
                **({"origin": "branch"} if str(row.get("OrigenSucursal")).lower() == "true" else {}),
            }
            for row in rows
            if row.get("IdOperativa")
        ]
        count = len(contracts)
        return {
            "detail": f"OCA aceptó el usuario y la clave: {count} operativa{'s' if count != 1 else ''}.",
            "contracts": contracts,
        }

    # --- Remoto ------------------------------------------------------------

    def checkout_problems(self, account):
        problems = []
        if not re.fullmatch(r"\d{2}-\d{8}-\d", format_cuit(account.sender_document)):
            problems.append("a tu cuenta de OCA le falta el CUIT del remitente")
        if not common.postal_code_digits(account.origin_postal_code):
            problems.append("a tu cuenta de OCA le falta el código postal de origen")
        return problems

    def _tariff(self, account, contract, postal_code, *, weight_kg, packages, timeout=None):
        """``Tarifar_Envio_Corporativo`` -> ``(precio, días)``. Pide el CUIT y
        el CP de origen de la cuenta (``ShipmentError`` si faltan)."""
        problems = self.checkout_problems(account)
        if problems:
            raise ShipmentError("Para cotizar con OCA cargá " + problems[0].split("le falta ", 1)[1] + " en tu cuenta.")
        length, width, height = common.box_cm(account)
        row = OcaClient(account, timeout=timeout).quote(
            cuit=format_cuit(account.sender_document),
            operativa=contract["code"],
            weight_kg=float(weight_kg),
            volume_m3=length * width * height * packages / 1_000_000,
            origin_postal_code=common.postal_code_digits(account.origin_postal_code),
            postal_code=common.postal_code_digits(postal_code),
            packages=packages,
        )
        price = common.decimal_or_none(row.get("Total"))
        if price is None or price <= 0:
            raise ShipmentError("OCA no devolvió el precio del envío.")
        days = row.get("PlazoEntrega")
        return price.quantize(Decimal("0.01")), (int(days) if str(days or "").isdigit() else None)

    def quote(self, account, order, contract, *, package_count=1):
        count, weight = common.split_packages(order, account, package_count)
        price, days = self._tariff(account, contract, order.address.postal_code, weight_kg=weight * count, packages=count)
        return {"price": price, "price_without_tax": None, "insurance": None, "chargeable_weight_kg": None, "delivery_days": days}

    def checkout_quote(self, account, contract, postal_code, weight_kg, *, timeout=None):
        return self._tariff(account, contract, postal_code, weight_kg=weight_kg, packages=1, timeout=timeout)[0]

    def build_xml(self, account, order, contract, *, branch_id="", package_count=1):
        """El ``XML_Datos`` de la orden de retiro, como en el ejemplo oficial."""
        extra = account.extra or {}
        address = order.address
        root = ET.Element("ROWS")
        ET.SubElement(root, "cabecera", ver="2.0", nrocuenta=fit(account.client_code, 20))
        origins = ET.SubElement(root, "origenes")
        from_branch = contract.get("origin") == "branch"
        origin_state = oca_province(account.origin_state, account.origin_postal_code)
        origin = ET.SubElement(
            origins,
            "origen",
            calle=fit(account.origin_street, 30),
            nro=fit(account.origin_number, 5),
            piso=fit(account.origin_floor, 6),
            depto=fit(account.origin_apartment, 4),
            cp=common.postal_code_digits(account.origin_postal_code),
            localidad=fit(account.origin_city, 30),
            provincia=fit(origin_state, 30),
            contacto=fit(account.sender_name, 30),
            email=fit(account.sender_email, 100),
            solicitante=fit(account.sender_name, 30),
            observaciones="",
            centrocosto=fit(extra.get("cost_center") or "0", 10),
            idfranjahoraria=fit(extra.get("pickup_slot") or "1", 1),
            idcentroimposicionorigen=fit(extra.get("origin_branch_id") or "0", 10) if from_branch else "0",
            fecha=next_business_day().strftime("%Y%m%d"),
        )
        shipments = ET.SubElement(origin, "envios")
        shipment = ET.SubElement(shipments, "envio", idoperativa=str(contract["code"]), nroremito=fit(common.order_number(order), 30))
        first_name, last_name = split_name(address.recipient_name)
        state = oca_province(address.state, address.postal_code)
        ET.SubElement(
            shipment,
            "destinatario",
            apellido=fit(last_name, 30),
            nombre=fit(first_name, 30),
            calle=fit(address.street, 30),
            nro=fit(address.number, 5) or "S/N",
            piso="",
            depto="",
            localidad=fit(address.city, 30),
            provincia=fit(state, 30),
            cp=common.postal_code_digits(address.postal_code),
            telefono=fit(order.contact_phone, 30),
            email=fit(order.contact_email, 100),
            idci=str(branch_id or "0"),
            celular=fit(order.contact_phone, 15),
            # Piso, depto, entre calles: van como observación para el repartidor.
            observaciones=fit(address.reference, 100),
        )
        packages = ET.SubElement(shipment, "paquetes")
        count, weight = common.split_packages(order, account, package_count)
        length, width, height = common.box_cm(account)
        for _index in range(count):
            ET.SubElement(packages, "paquete", alto=str(height), ancho=str(width), largo=str(length), peso=f"{weight:.3f}", valor="0", cant="1")
        body = ET.tostring(root, encoding="unicode")
        return '<?xml version="1.0" encoding="iso-8859-1" standalone="yes"?>' + body

    def submit(self, account, order, contract, *, branch_id="", package_count=1):
        result = OcaClient(account).create(self.build_xml(account, order, contract, branch_id=branch_id, package_count=package_count))
        accepted = result["accepted"]
        if not accepted:
            reason = result["rejected"][0] if result["rejected"] else "OCA no ingresó el envío."
            raise ShipmentError(f"OCA rechazó el envío: {reason}")
        row = accepted[0]
        # El QR (una imagen en base64) no se guarda: pesa y está en la etiqueta.
        raw = {"summary": result["summary"], "detail": {key: value for key, value in row.items() if key != "QR"}}
        return CreatedShipment(
            tracking_number=str(row.get("NumeroEnvio") or ""),
            group_number=str(row.get("OrdenRetiro") or ""),
            package_count=max(int(package_count or 1), 1),
            status_text=str(row.get("Estado") or ""),
            raw=raw,
        )

    def label(self, shipment, fmt="pdf"):
        return OcaClient(shipment.account).label(pickup_order=shipment.group_number, number=shipment.tracking_number, fmt=fmt)

    def fetch_events(self, shipment):
        events = []
        for row in OcaClient(shipment.account).traces(shipment.tracking_number):
            moment = _moment(row.get("Fecha"))
            if moment is None:
                continue
            text = str(row.get("Estado") or "")
            status, order_status = classify(row.get("IdEstado"), text)
            events.append(
                CarrierEvent(
                    occurred_at=moment,
                    event=f"{row.get('IdEstado') or ''} {text}".strip()[:80],
                    status=status,
                    order_status=order_status,
                    reason="" if _plain(row.get("Motivo")) == "sin motivo" else str(row.get("Motivo") or ""),
                    status_text=text,
                    branch=str(row.get("Sucursal") or ""),
                )
            )
        return events

    def cancel_remote(self, shipment):
        if not shipment.group_number:
            raise ShipmentError("Este envío de OCA no tiene la orden de retiro: anulalo desde e-Pak.")
        OcaClient(shipment.account).cancel(shipment.group_number)

    def branches(self, account, postal_code, state=""):
        results = []
        for center in OcaClient(account).branches(common.postal_code_digits(postal_code)):
            # Solo las que entregan paquetes pueden ser destino.
            if not any("entrega" in _plain(service) for service in center.get("services") or []):
                continue
            results.append(
                {
                    "id": str(center.get("IdCentroImposicion") or ""),
                    "code": str(center.get("Sigla") or ""),
                    "name": str(center.get("Sucursal") or "").strip(),
                    "address": " ".join(str(center.get(key) or "").strip() for key in ("Calle", "Numero")).strip(),
                    "city": str(center.get("Localidad") or "").strip(),
                    "postal_code": str(center.get("CodigoPostal") or "").strip(),
                    "hours": str(center.get("HorarioAtencion") or "").strip(),
                    "is_hop": False,
                }
            )
        return [branch for branch in results if branch["id"]]


__all__ = ["OcaProvider", "OcaError"]
