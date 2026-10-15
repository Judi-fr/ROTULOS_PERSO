"""Correo Argentino en la capa común de transportistas: Paq.ar despacha y
MiCorreo cotiza (``client.py`` explica por qué las dos).

- **Los "contratos"** son los servicios de Paq.ar (``serviceType``: ``CP`` =
  Clásico) con su tipo de entrega. Como un mismo servicio va a domicilio o a
  sucursal, el código guardado es ``<servicio>-D`` o ``<servicio>-S``
  (``normalize_contracts``); el comerciante solo escribe el servicio.
- **Un solo paquete por orden:** Paq.ar toma únicamente el primero de
  ``parcels`` (lo dice su manual), así que varios bultos se mandan como uno
  con el peso total.
- **La provincia** es obligatoria (código de una letra) para el remitente y,
  a domicilio, para el destinatario: sale de lo que escribió el comprador o
  de la letra del CPA (``common.province_code``).
- **Estados:** el manual solo muestra PREIMPOSICION, CADUCA y EN PROCESO DE
  CANCELACION; el resto se traduce por el texto (A CONFIRMAR).
"""

from __future__ import annotations

import unicodedata
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.orders.models import Order

from .. import common
from ..base import AccountField, CarrierEvent, CarrierProvider, CreatedShipment, ShipmentError
from ..models import CarrierShipment
from .client import MiCorreoClient, PaqarClient

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
DELIVERY_CODES = {"home": "D", "branch": "S"}
# Paq.ar pesa en gramos con 5 dígitos como máximo.
MAX_GRAMS = 99999


def _plain(value):
    return unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().upper()


def service_of(contract):
    return str(contract.get("service") or str(contract.get("code") or "").split("-")[0] or "CP")


def classify(status_id, text):
    """``(estado del envío, estado del pedido)`` de un evento de Paq.ar."""
    plain = f"{_plain(status_id)} {_plain(text)}"
    if "PREIMPOSIC" in plain or plain.strip().startswith("PRE "):
        return None, None
    if "CANCEL" in plain or "CADUC" in plain or "ANULAD" in plain:
        return CarrierShipment.Status.CANCELLED, None
    if "DEVUEL" in plain or "DEVOLUC" in plain or "REMITENTE" in plain:
        return CarrierShipment.Status.RETURNING, None
    if "NO ENTREGAD" in plain or "SINIESTR" in plain or "EXTRAVI" in plain or "RECHAZ" in plain:
        return CarrierShipment.Status.ISSUE, None
    if "ENTREGAD" in plain:
        return CarrierShipment.Status.DELIVERED, Order.Status.DELIVERED
    if "ESPERA" in plain or "DISPONIBLE" in plain or "PARA RETIRAR" in plain:
        return CarrierShipment.Status.AT_BRANCH, Order.Status.IN_TRANSIT
    if "IMPOSIC" in plain or "ADMISI" in plain or "INGRES" in plain:
        # Se lo dejaron al Correo: está despachado.
        return CarrierShipment.Status.IN_TRANSIT, Order.Status.DISPATCHED
    return CarrierShipment.Status.IN_TRANSIT, Order.Status.IN_TRANSIT


def _moment(value):
    """``28-06-2022 11:53`` (como en el ejemplo) o ISO con zona."""
    text = str(value or "").strip()
    if not text:
        return None
    moment = parse_datetime(text)
    if moment is None:
        try:
            moment = datetime.strptime(text, "%d-%m-%Y %H:%M")
        except ValueError:
            return None
    if timezone.is_naive(moment):
        moment = moment.replace(tzinfo=AR_TZ)
    return moment


class CorreoArgentinoProvider(CarrierProvider):
    code = "correo_argentino"
    name = "Correo Argentino"
    account_help = (
        "Para despachar se usa Paq.ar: el número de acuerdo y la API key te los da el área comercial de "
        "Correo Argentino. Para ver el precio antes de despachar hacen falta además las credenciales de "
        "la API de MiCorreo y tu customerId."
    )
    account_fields = (
        AccountField("client_code", "Número de acuerdo (Paq.ar)", storage="column", required=True, max_length=50),
        AccountField("password", "API key de Paq.ar", storage="password", required=True),
        AccountField(
            "declared_value",
            "Valor declarado por envío ($)",
            max_length=12,
            help="Paq.ar lo pide en cada envío. Si lo dejás vacío se informa 0.",
        ),
        AccountField("micorreo_user", "Usuario de la API de MiCorreo", required=True, required_for="quote", max_length=100),
        AccountField("micorreo_password", "Clave de la API de MiCorreo", storage="secret", required=True, required_for="quote"),
        AccountField(
            "customer_id",
            "customerId de MiCorreo",
            required=True,
            required_for="quote",
            max_length=20,
            help="El número de cliente de tu cuenta MiCorreo; Correo Argentino te lo da con las credenciales.",
        ),
    )
    contract_label = "Servicio"
    contract_code_label = "Tipo de servicio"
    contract_one = "un servicio"
    contract_help = "CP es Correo Argentino Clásico. Agregá uno a domicilio y otro a sucursal si usás los dos."
    default_contracts = (
        {"code": "CP", "label": "Clásico a domicilio", "kind": "home"},
        {"code": "CP", "label": "Clásico a sucursal", "kind": "branch"},
    )
    label_name = "rótulo de Correo Argentino"
    supports_checkout = True
    checkout_default_name = "Correo Argentino a domicilio"

    def tracking_url(self, number):
        template = str(
            getattr(settings, "CORREO_ARGENTINO_TRACKING_URL_TEMPLATE", "https://www.correoargentino.com.ar/formularios/e-commerce?id={numero}")
        )
        return template.format(numero=number)

    def normalize_contracts(self, contracts):
        prepared = []
        for item in contracts or []:
            item = dict(item) if isinstance(item, dict) else {}
            service = str(item.get("service") or item.get("code") or "").strip().upper()
            # Lo que vuelve de la página ya puede traer el sufijo.
            if service.endswith(("-D", "-S")):
                service = service[:-2]
            kind = str(item.get("kind") or "home")
            if not service:
                continue
            if len(service) > 2:
                raise ValueError("El tipo de servicio de Correo Argentino tiene hasta dos letras (por ejemplo CP).")
            item["code"] = f"{service}-{DELIVERY_CODES.get(kind, 'D')}"
            prepared.append(item)
        result = super().normalize_contracts(prepared)
        for contract in result:
            contract["service"] = contract["code"].split("-")[0]
        return result

    def account_problems(self, account):
        problems = super().account_problems(account)
        if not common.province_code(account.origin_state, account.origin_postal_code):
            problems.append("la provincia de origen")
        return problems

    def test_credentials(self, account):
        PaqarClient(account).check_credentials()
        detail = "Paq.ar aceptó el acuerdo y la API key."
        if not self.credential_problems(account, "quote"):
            MiCorreoClient(account).login()
            detail += " MiCorreo también aceptó sus credenciales: se puede cotizar."
        else:
            detail += " Sin las credenciales de MiCorreo no se cotiza, pero se puede despachar."
        return {"detail": detail}

    # --- Remoto ------------------------------------------------------------

    def _grams(self, order, account):
        grams = int(common.order_weight_kg(order, account) * 1000)
        return min(max(grams, 1), MAX_GRAMS)

    def checkout_problems(self, account):
        if not common.postal_code_digits(account.origin_postal_code):
            return ["a tu cuenta de Correo Argentino le falta el código postal de origen"]
        return []

    def _rate(self, account, contract, postal_code, grams, *, timeout=None):
        """MiCorreo ``/rates`` -> ``(precio, días)`` del servicio y tipo de
        entrega de ``contract``."""
        origin = common.postal_code_digits(account.origin_postal_code)
        if not origin:
            raise ShipmentError("Para cotizar con Correo Argentino cargá el código postal de origen en tu cuenta.")
        length, width, height = common.box_cm(account)
        delivered_type = DELIVERY_CODES.get(contract.get("kind"), "D")
        rates = MiCorreoClient(account, timeout=timeout).rates(
            {
                "customerId": str((account.extra or {}).get("customer_id") or ""),
                "postalCodeOrigin": origin,
                "postalCodeDestination": common.postal_code_digits(postal_code),
                "deliveredType": delivered_type,
                "dimensions": {"weight": min(max(int(grams), 1), 25000), "height": height, "width": width, "length": length},
            }
        )
        service = service_of(contract)
        matching = [
            rate
            for rate in rates
            if str(rate.get("deliveredType") or delivered_type) == delivered_type and str(rate.get("productType") or service) == service
        ]
        if not matching:
            raise ShipmentError("Correo Argentino no cotizó ese servicio para ese destino.")
        rate = matching[0]
        price = common.decimal_or_none(rate.get("price"))
        if price is None or price <= 0:
            raise ShipmentError("Correo Argentino no devolvió el precio del envío.")
        days = rate.get("deliveryTimeMax") or rate.get("deliveryTimeMin")
        return price.quantize(Decimal("0.01")), (int(days) if str(days or "").isdigit() else None)

    def quote(self, account, order, contract, *, package_count=1):
        price, days = self._rate(account, contract, order.address.postal_code, self._grams(order, account))
        return {"price": price, "price_without_tax": None, "insurance": None, "chargeable_weight_kg": None, "delivery_days": days}

    def checkout_quote(self, account, contract, postal_code, weight_kg, *, timeout=None):
        return self._rate(account, contract, postal_code, Decimal(weight_kg) * 1000, timeout=timeout)[0]

    def build_order(self, account, order, contract, *, branch_id=""):
        """El cuerpo de ``POST /orders`` según el esquema del manual de Paq.ar."""
        address = order.address
        origin_state = common.province_code(account.origin_state, account.origin_postal_code)
        state = common.province_code(address.state, address.postal_code)
        if not branch_id and not state:
            raise ShipmentError("No se reconoce la provincia del pedido: Correo Argentino la necesita para entregar a domicilio.")
        length, width, height = common.box_cm(account)
        declared = common.decimal_or_none((account.extra or {}).get("declared_value")) or Decimal("0")
        created = timezone.localtime(order.created_at, AR_TZ) if order.created_at else timezone.localtime(timezone.now(), AR_TZ)
        return {
            "sellerId": "",
            "order": {
                "senderData": {
                    "id": "",
                    "businessName": common.text(account.sender_name, 100),
                    "areaCodePhone": "",
                    "phoneNumber": common.text(account.sender_phone, 30),
                    "areaCodeCellphone": "",
                    "cellphoneNumber": "",
                    "email": common.text(account.sender_email, 100),
                    "observation": "",
                    "address": {
                        "streetName": common.text(account.origin_street, 60),
                        "streetNumber": common.text(account.origin_number, 10),
                        "cityName": common.text(account.origin_city, 60),
                        "floor": common.text(account.origin_floor, 3),
                        "department": common.text(account.origin_apartment, 3),
                        "state": origin_state,
                        "zipCode": common.postal_code_digits(account.origin_postal_code),
                    },
                },
                "shippingData": {
                    "name": common.text(address.recipient_name, 100),
                    "areaCodePhone": "",
                    "phoneNumber": common.text(order.contact_phone, 30),
                    "areaCodeCellphone": "",
                    "cellphoneNumber": "",
                    "email": common.text(order.contact_email, 100),
                    # Piso, depto, entre calles: para el cartero.
                    "observation": common.text(address.reference, 100),
                    "address": {
                        "streetName": common.text(address.street, 60),
                        "streetNumber": common.text(address.number, 10) or "S/N",
                        "cityName": common.text(address.city, 60),
                        "floor": "",
                        "department": "",
                        "state": state,
                        "zipCode": common.postal_code_digits(address.postal_code),
                    },
                },
                # Paq.ar toma un solo paquete: va con el peso total del pedido.
                "parcels": [
                    {
                        "dimensions": {"height": str(min(height, 999)), "width": str(min(width, 999)), "depth": str(min(length, 999))},
                        "productWeight": str(self._grams(order, account)),
                        "productCategory": "",
                        "declaredValue": str(int(declared)),
                    }
                ],
                "deliveryType": "agency" if branch_id else "homeDelivery",
                "agencyId": str(branch_id or ""),
                "saleDate": created.strftime("%Y-%m-%dT%H:%M:%S-03:00"),
                "shipmentClientId": common.text(common.order_number(order), 30),
                "serviceType": service_of(contract),
            },
        }

    def submit(self, account, order, contract, *, branch_id="", package_count=1):
        data = PaqarClient(account).create_order(self.build_order(account, order, contract, branch_id=branch_id))
        # A CONFIRMAR: el nombre del campo con el número de seguimiento.
        number = ""
        for key in ("trackingNumber", "tracking_number", "tn", "TN"):
            if data.get(key):
                number = str(data[key])
                break
        if not number and isinstance(data.get("order"), dict):
            number = str(data["order"].get("trackingNumber") or "")
        return CreatedShipment(tracking_number=number, package_count=1, status_text=str(data.get("status") or "Preimpuesto"), raw=data)

    def label(self, shipment, fmt="pdf"):
        labels = PaqarClient(shipment.account).labels([shipment.tracking_number])
        content = labels.get(shipment.tracking_number) or next(iter(labels.values()), b"")
        if not content:
            raise ShipmentError(f"Correo Argentino no dio el rótulo de {shipment.tracking_number}.")
        return content

    def fetch_events(self, shipment):
        events = []
        for item in PaqarClient(shipment.account).tracking([shipment.tracking_number]):
            if str(item.get("trackingNumber") or shipment.tracking_number) != shipment.tracking_number:
                continue
            raw_events = item.get("event") or item.get("events") or []
            for event in raw_events if isinstance(raw_events, list) else [raw_events]:
                if not isinstance(event, dict):
                    continue
                moment = _moment(event.get("date"))
                if moment is None:
                    continue
                text = str(event.get("status") or event.get("event") or "")
                status, order_status = classify(event.get("statusId"), text)
                events.append(
                    CarrierEvent(
                        occurred_at=moment,
                        event=str(event.get("statusId") or text)[:80],
                        status=status,
                        order_status=order_status,
                        status_text=text,
                        branch=str(event.get("facility") or event.get("branch") or ""),
                    )
                )
        return events

    def cancel_remote(self, shipment):
        PaqarClient(shipment.account).cancel(shipment.tracking_number)

    def branches(self, account, postal_code, state=""):
        code = common.province_code(state, postal_code)
        if not code:
            raise ShipmentError("Para buscar sucursales del Correo hace falta la provincia del pedido.")
        wanted = common.postal_code_digits(postal_code)
        results = []
        for agency in PaqarClient(account).agencies(code):
            location = agency.get("location") or {}
            zip_code = str(location.get("zip_code") or "")
            results.append(
                {
                    "id": str(agency.get("agency_id") or ""),
                    "code": str(agency.get("agency_id") or ""),
                    "name": str(agency.get("agency_name") or "").strip(),
                    "address": " ".join(str(location.get(key) or "").strip() for key in ("street_name", "street_number")).strip(),
                    "city": str(location.get("city_name") or "").strip(),
                    "postal_code": zip_code,
                    "hours": str(agency.get("schedule") or "").strip(),
                    "is_hop": False,
                    "_rank": 0 if common.postal_code_digits(zip_code) == wanted else (1 if common.postal_code_digits(zip_code)[:2] == wanted[:2] else 2),
                }
            )
        # La provincia entera son cientos: primero las del mismo CP y las cercanas.
        results = sorted((branch for branch in results if branch["id"]), key=lambda branch: (branch["_rank"], branch["name"]))
        nearby = [branch for branch in results if branch["_rank"] < 2] or results
        for branch in nearby:
            branch.pop("_rank", None)
        return nearby[:50]
