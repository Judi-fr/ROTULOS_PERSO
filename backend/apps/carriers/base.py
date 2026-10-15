"""Lo que tiene que saber hacer un transportista para que se pueda despachar con
él desde Mis pedidos: la interfaz común (``CarrierProvider``) y lo que es igual
para todos (validar el pedido, guardar el envío, cargarle el seguimiento al
pedido, aplicar los movimientos solo hacia adelante, cancelar).

Como ``apps.integrations.providers.base.StoreProvider`` para las tiendas: lo que
cambia entre transportistas vive en su carpeta (``andreani/``,
``correo_argentino/``, ``oca/``) como atributos y métodos de su proveedor, y las
vistas (``views.py``) son una sola para todos.

Un proveedor nuevo implementa los métodos "remotos" (``submit``, ``quote``,
``label``, ``fetch_events``, ``cancel_remote``, ``branches``,
``test_credentials``) y declara qué pide su cuenta (``account_fields``); lo
demás lo hereda. Andreani, que existía antes que esta capa, reimplementa la
orquestación con su propio código (``andreani/provider.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.orders.models import Order
from apps.orders.shipping import apply_shipping

from . import common
from .models import CarrierShipment, CarrierShipmentEvent

logger = logging.getLogger(__name__)


class CarrierError(Exception):
    """Falla hablando con el transportista (red, 5xx): en general se puede reintentar."""


class CarrierAuthError(CarrierError):
    """El transportista rechazó las credenciales: reintentar no lo arregla."""


class CarrierRejectedError(CarrierError):
    """El transportista rechazó el pedido por inválido; el mensaje es suyo."""


class ShipmentError(Exception):
    """El envío no se puede crear o actualizar; el mensaje es para el comerciante."""


@dataclass(frozen=True)
class AccountField:
    """Un dato de la cuenta que el comerciante carga en ``transportista.html``.

    ``storage``: ``"column"`` (un campo de ``CarrierAccount``: ``username``,
    ``client_code``, ``sender_document``), ``"password"`` (la columna cifrada
    ``password``), ``"secret"`` (``CarrierAccount.secret(name)``) o ``"extra"``
    (``CarrierAccount.extra[name]``)."""

    name: str
    label: str
    storage: str = "extra"
    required: bool = False
    help: str = ""
    max_length: int = 150
    # Solo lo piden algunas operaciones (cotizar): no bloquea despachar.
    required_for: str = "dispatch"
    # Opciones fijas (``((valor, texto), ...)``): la página muestra un select.
    choices: tuple = ()

    @property
    def is_secret(self):
        return self.storage in ("password", "secret")


@dataclass
class CreatedShipment:
    """Lo que devuelve el transportista al crear un envío."""

    tracking_number: str
    group_number: str = ""
    package_count: int = 1
    status_text: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class CarrierEvent:
    """Un movimiento del envío ya traducido: ``status`` es el estado del envío
    al que lleva (o ``None`` si no dice nada nuevo) y ``order_status`` el del
    pedido (o ``None``)."""

    occurred_at: datetime
    event: str
    status: str | None = None
    order_status: str | None = None
    cycle: str = ""
    reason: str = ""
    sub_reason: str = ""
    status_text: str = ""
    branch: str = ""
    comment: str = ""


def _lower_first(text):
    """"Usuario de e-Pak" -> "usuario de e-Pak", pero "API key" queda igual:
    solo la inicial, y no en una sigla."""
    if len(text) > 1 and text[1].isupper():
        return text
    return text[:1].lower() + text[1:]


STATUS_RANK = {
    CarrierShipment.Status.PENDING: 0,
    CarrierShipment.Status.IN_TRANSIT: 1,
    CarrierShipment.Status.AT_BRANCH: 2,
    CarrierShipment.Status.ISSUE: 3,
    CarrierShipment.Status.RETURNING: 4,
    CarrierShipment.Status.DELIVERED: 5,
    CarrierShipment.Status.CANCELLED: 6,
}

CONTRACT_KINDS = {"home", "branch"}


class CarrierProvider:
    code = ""
    name = ""
    # Texto de ayuda de la página de la cuenta (dónde se sacan las credenciales).
    account_help = ""
    account_fields: tuple[AccountField, ...] = ()
    # Cómo llama el transportista a lo que acá es un "contrato" (un servicio).
    contract_label = "Contrato"
    contract_code_label = "Número de contrato"
    # "al menos ..." en los avisos: con su artículo, que cambia con el género.
    contract_one = "un contrato"
    contract_help = ""
    # Si "Probar conexión" trae los contratos de la cuenta (OCA: sus operativas).
    contracts_from_test = False
    # Con qué contratos arranca una cuenta nueva (Correo Argentino: el Clásico).
    default_contracts: tuple = ()
    # Andreani pide el volumen; Correo Argentino y OCA, las medidas de la caja.
    uses_volume = False
    uses_dimensions = True
    # Correo Argentino pide un valor declarado obligatorio.
    uses_declared_value = False
    branch_label = "sucursal"
    supports_quote = True
    supports_branches = True
    supports_cancel = True
    label_formats = ("pdf",)
    # Lo que dice el texto del botón de imprimir en Mis pedidos.
    label_name = "etiqueta"
    # Si se puede ofrecer en el checkout de las tiendas (``checkout.py``): el
    # proveedor implementa ``checkout_quote``.
    supports_checkout = False
    checkout_default_name = ""
    # Si el transportista puede avisarnos las novedades de los envíos
    # (``push_views.py``) en vez de que las consultemos.
    supports_push = False
    push_help = ""

    # --- Cuenta -----------------------------------------------------------

    def tracking_url(self, number):
        return ""

    def field(self, name):
        return next((item for item in self.account_fields if item.name == name), None)

    def field_value(self, account, item):
        if item.storage == "column":
            return getattr(account, item.name)
        if item.storage == "extra":
            return str((account.extra or {}).get(item.name) or "")
        return ""

    def has_field(self, account, item):
        if item.storage == "password":
            return bool(account.password_encrypted)
        if item.storage == "secret":
            return account.has_secret(item.name)
        return bool(str(self.field_value(account, item) or "").strip())

    def credential_problems(self, account, purpose="dispatch"):
        return [
            _lower_first(item.label)
            for item in self.account_fields
            if item.required and item.required_for in (purpose, "all") and not self.has_field(account, item)
        ]

    def account_problems(self, account):
        """Lo que le falta a la cuenta para poder crear envíos (textos)."""
        problems = []
        missing = self.credential_problems(account)
        if missing:
            problems.append(", ".join(missing))
        if not account.contracts:
            problems.append(f"al menos {self.contract_one}")
        if not account.sender_name:
            problems.append("el nombre del remitente")
        if not (account.origin_street and account.origin_number and account.origin_city and common.postal_code_digits(account.origin_postal_code)):
            problems.append("el domicilio de origen (calle, número, localidad y código postal)")
        return problems

    def normalize_contracts(self, contracts):
        """Los contratos que manda la página, validados. ``ValueError`` con el
        motivo si alguno no sirve."""
        result, seen = [], set()
        for item in contracts or []:
            item = item if isinstance(item, dict) else {}
            code = str(item.get("code") or "").strip()
            kind = str(item.get("kind") or "home")
            if not code:
                continue
            if kind not in CONTRACT_KINDS:
                raise ValueError("Cada servicio es de entrega a domicilio o en sucursal.")
            if code in seen:
                raise ValueError(f"El {self.contract_label.lower()} {code} está repetido.")
            seen.add(code)
            contract = {"code": code[:50], "label": str(item.get("label") or "").strip()[:80], "kind": kind}
            if item.get("origin") == "branch":
                contract["origin"] = "branch"
            result.append(contract)
        return result

    def test_credentials(self, account):
        """Prueba las credenciales. Devuelve ``{"detail", "contracts"?}`` o lanza
        ``CarrierError``."""
        raise NotImplementedError

    # --- Operaciones remotas ----------------------------------------------

    def quote(self, account, order, contract, *, package_count=1):
        """``{"price": Decimal con IVA, "price_without_tax"?, "delivery_days"?}``."""
        raise ShipmentError(f"{self.name} no cotiza desde acá.")

    def submit(self, account, order, contract, *, branch_id="", package_count=1):
        """Crea el envío en el transportista -> ``CreatedShipment``."""
        raise NotImplementedError

    def label(self, shipment, fmt="pdf"):
        """La etiqueta del transportista (bytes)."""
        raise NotImplementedError

    def fetch_events(self, shipment):
        """Los movimientos del envío -> lista de ``CarrierEvent``."""
        raise NotImplementedError

    def checkout_quote(self, account, contract, postal_code, weight_kg, *, timeout=None):
        """El costo (``Decimal``) de un envío a domicilio de ``weight_kg`` a
        ``postal_code`` (4 dígitos), sin pedido: lo que se cotiza en el
        checkout. ``timeout`` corto: está en medio de una venta."""
        raise NotImplementedError

    def checkout_problems(self, account):
        """Lo que le falta a la cuenta para cotizar en el checkout, además de
        las credenciales de cotizar (textos)."""
        return []

    def push_number(self, payload):
        """El número de envío de una novedad push (``""`` si no trae)."""
        return ""

    def receive_push(self, shipment, payload):
        """Aplica una novedad push al envío. Devuelve si cambió su estado."""
        raise NotImplementedError

    def cancel_remote(self, shipment):
        raise CarrierRejectedError(f"{self.name} no permite cancelar desde acá.")

    def branches(self, account, postal_code, state=""):
        """Sucursales que entregan en ese código postal (``state``: la
        provincia del pedido, Correo Argentino busca por provincia): dicts con ``id``,
        ``code``, ``name``, ``address``, ``city``, ``postal_code``, ``hours``,
        ``is_hop``."""
        return []

    # --- Orquestación común -----------------------------------------------

    def quote_order(self, account, order, contract_code, *, package_count=1):
        if self.credential_problems(account, "quote"):
            raise ShipmentError(f"Para cotizar, a tu cuenta de {self.name} le falta " + ", ".join(self.credential_problems(account, "quote")) + ".")
        contract = account.contract(contract_code)
        if contract is None:
            raise ShipmentError(f"Ese {self.contract_label.lower()} no está en tu cuenta de {self.name}.")
        if not common.postal_code_digits(order.address.postal_code if order.address_id else ""):
            raise ShipmentError(f"El pedido no tiene código postal: {self.name} cotiza por destino.")
        try:
            return self.quote(account, order, contract, package_count=package_count)
        except CarrierError as exc:
            raise ShipmentError(str(exc)) from exc

    def validate_new_shipment(self, account, order, contract_code, branch_id):
        problems = self.account_problems(account)
        if problems:
            raise ShipmentError(f"A tu cuenta de {self.name} le falta " + ", ".join(problems) + ".")
        contract = account.contract(contract_code)
        if contract is None:
            raise ShipmentError(f"Ese {self.contract_label.lower()} no está en tu cuenta de {self.name}.")
        if contract.get("kind") == "branch" and not branch_id:
            raise ShipmentError(f"Para entregar en {self.branch_label} hay que elegir la {self.branch_label}.")
        if order.status in (Order.Status.CANCELLED, Order.Status.DELIVERED):
            raise ShipmentError("El pedido está cancelado o ya fue entregado.")
        open_shipment = order.carrier_shipments.exclude(status=CarrierShipment.Status.CANCELLED).first()
        if open_shipment is not None:
            raise ShipmentError(f"El pedido ya tiene el envío de {open_shipment.get_carrier_display()} {open_shipment.tracking_number}.")
        address = order.address
        if not branch_id and not (address.street and address.city and common.postal_code_digits(address.postal_code)):
            raise ShipmentError(f"El pedido no tiene calle, localidad y código postal: {self.name} los necesita para entregar.")
        if not address.recipient_name:
            raise ShipmentError("El pedido no tiene destinatario.")
        return contract

    def create_shipment(self, request, account, order, contract_code, *, branch_id="", branch_name="", package_count=1):
        """Crea el envío en el transportista y lo deja asociado al pedido, que
        pasa a "en preparación" con transportista, número y link. Devuelve el
        ``CarrierShipment``; ``ShipmentError`` con el motivo si no se puede."""
        contract = self.validate_new_shipment(account, order, contract_code, branch_id)
        quoted_price = None
        if self.supports_quote and not self.credential_problems(account, "quote"):
            try:
                quoted_price = self.quote(account, order, contract, package_count=package_count)["price"]
            except (CarrierError, ShipmentError) as exc:
                # La cotización es informativa: sin precio el envío sale igual.
                logger.info("No se pudo cotizar el pedido %s en %s: %s", order.pk, self.name, exc)
        try:
            created = self.submit(account, order, contract, branch_id=str(branch_id or ""), package_count=package_count)
        except CarrierError as exc:
            raise ShipmentError(str(exc)) from exc
        if not created.tracking_number:
            raise ShipmentError(f"{self.name} creó el envío pero no devolvió el número de seguimiento.")
        with transaction.atomic():
            shipment = CarrierShipment.objects.create(
                account=account,
                order=order,
                carrier=self.code,
                contract=str(contract["code"]),
                delivery_kind=CarrierShipment.DeliveryKind.BRANCH if branch_id else CarrierShipment.DeliveryKind.HOME,
                branch_id=str(branch_id or ""),
                branch_name=common.text(branch_name, 150),
                tracking_number=created.tracking_number,
                group_number=created.group_number,
                package_count=created.package_count or 1,
                carrier_status=common.text(created.status_text, 120),
                quoted_price=quoted_price,
                raw_response=created.raw,
            )
            target = order.status
            if Order.status_rank(Order.Status.PREPARING) > Order.status_rank(order.status):
                target = Order.Status.PREPARING
            apply_shipping(
                request,
                order,
                {
                    "status": target,
                    "carrier": self.name,
                    "tracking_number": created.tracking_number,
                    "tracking_url": self.tracking_url(created.tracking_number),
                },
            )
        return shipment

    def apply_events(self, shipment, events):
        """Guarda los movimientos nuevos y mueve envío y pedido SOLO hacia
        adelante (la devolución y la cancelación pisan a todo). Devuelve si el
        estado del envío cambió."""
        events = sorted(events, key=lambda item: item.occurred_at)
        for item in events:
            CarrierShipmentEvent.objects.get_or_create(
                shipment=shipment,
                occurred_at=item.occurred_at,
                event=common.text(item.event, 80),
                defaults={
                    "cycle": common.text(item.cycle, 50),
                    "reason": common.text(item.reason, 150),
                    "sub_reason": common.text(item.sub_reason, 150),
                    "status_text": common.text(item.status_text, 150),
                    "branch": common.text(item.branch, 150),
                    "comment": common.text(item.comment, 500),
                },
            )
        new_status = shipment.status
        order_target = None
        for item in events:
            candidate = item.status
            if candidate and (
                candidate in (CarrierShipment.Status.RETURNING, CarrierShipment.Status.CANCELLED)
                or (STATUS_RANK[candidate] > STATUS_RANK[new_status] and new_status not in CarrierShipment.FINAL_STATUSES)
            ):
                new_status = candidate
            if item.order_status and (order_target is None or Order.status_rank(item.order_status) > Order.status_rank(order_target)):
                order_target = item.order_status
        changed = new_status != shipment.status
        fields = {"last_checked_at": timezone.now(), "last_error": ""}
        if events:
            last = events[-1]
            fields["carrier_status"] = common.text(last.status_text or last.reason or last.event, 120)
            fields["last_event_at"] = last.occurred_at
        if changed:
            fields["status"] = new_status
        for name, value in fields.items():
            setattr(shipment, name, value)
        shipment.save(update_fields=[*fields, "updated_at"])

        order = shipment.order
        if (
            order_target
            and order.tracking_number == shipment.tracking_number
            and order.status != Order.Status.CANCELLED
            and Order.status_rank(order_target) > Order.status_rank(order.status)
        ):
            apply_shipping(None, order, {"status": order_target})
        return changed

    def sync_shipment(self, shipment):
        """Consulta el seguimiento y actualiza envío y pedido. Los errores del
        transportista se propagan (el llamador decide si los anota y sigue)."""
        return self.apply_events(shipment, self.fetch_events(shipment))

    def due_shipments(self, now=None, limit=50):
        """Envíos abiertos de este transportista que hace rato no se consultan."""
        now = now or timezone.now()
        minutes = getattr(settings, f"{self.code.upper()}_TRACKING_POLL_MINUTES", getattr(settings, "CARRIER_TRACKING_POLL_MINUTES", 30))
        days = getattr(settings, f"{self.code.upper()}_TRACKING_MAX_DAYS", getattr(settings, "CARRIER_TRACKING_MAX_DAYS", 60))
        return (
            CarrierShipment.objects.filter(carrier=self.code, created_at__gte=now - timedelta(days=days), account__is_active=True)
            .exclude(status__in=CarrierShipment.FINAL_STATUSES)
            .filter(Q(last_checked_at__isnull=True) | Q(last_checked_at__lt=now - timedelta(minutes=minutes)))
            .select_related("account", "order")
            .order_by("last_checked_at")[:limit]
        )

    def cancel_shipment(self, request, shipment):
        """Pide la cancelación y, si se acepta, deja el envío cancelado y le
        saca el seguimiento al pedido (que vuelve a poder enviarse)."""
        if shipment.is_final:
            raise ShipmentError("El envío ya está entregado o cancelado.")
        try:
            self.cancel_remote(shipment)
        except CarrierError as exc:
            raise ShipmentError(str(exc)) from exc
        shipment.status = CarrierShipment.Status.CANCELLED
        shipment.carrier_status = "Cancelación solicitada"
        shipment.save(update_fields=["status", "carrier_status", "updated_at"])
        order = shipment.order
        if order.tracking_number == shipment.tracking_number:
            apply_shipping(request, order, {"status": order.status, "carrier": "", "tracking_number": "", "tracking_url": ""})
        return shipment

    # --- Para la página de la cuenta -------------------------------------

    def describe(self):
        """Lo que la página necesita saber del transportista."""
        return {
            "code": self.code,
            "name": self.name,
            "account_help": self.account_help,
            "fields": [
                {
                    "name": item.name,
                    "label": item.label,
                    "secret": item.is_secret,
                    "required": item.required,
                    "required_for": item.required_for,
                    "help": item.help,
                    "max_length": item.max_length,
                    "choices": [{"value": value, "label": label} for value, label in item.choices],
                }
                for item in self.account_fields
            ],
            "contract_label": self.contract_label,
            "contract_code_label": self.contract_code_label,
            "contract_help": self.contract_help,
            "contracts_from_test": self.contracts_from_test,
            "default_contracts": list(self.default_contracts),
            "uses_volume": self.uses_volume,
            "uses_dimensions": self.uses_dimensions,
            "uses_declared_value": self.uses_declared_value,
            "branch_label": self.branch_label,
            "supports_quote": self.supports_quote,
            "supports_branches": self.supports_branches,
            "supports_cancel": self.supports_cancel,
            "label_formats": list(self.label_formats),
            "label_name": self.label_name,
            "supports_push": self.supports_push,
            "supports_checkout": self.supports_checkout,
            "push_help": self.push_help,
        }
