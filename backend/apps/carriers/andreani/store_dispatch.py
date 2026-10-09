"""Despachar con Andreani desde el admin de la tienda (acción masiva de
Tiendanube, Shopify o WooCommerce; ver ``apps.integrations.store_print``).

Allá no hay un diálogo donde elegir contrato ni sucursal, así que se decide
solo: el contrato a domicilio que la tienda usa para cotizar en el checkout
(``checkout.config``), o si no el primero a domicilio de la cuenta. A
sucursal no se puede: necesitaría que alguien elija la sucursal de cada
pedido (eso queda en Mis pedidos). Un pedido que ya tiene su envío de
Andreani no se vuelve a crear: se imprime el que tiene.
"""

from __future__ import annotations

from ..models import CarrierShipment
from . import checkout
from .shipments import ShipmentError, account_problems, create_shipment


class DispatchError(Exception):
    """No se puede despachar ninguno; el mensaje es para el comerciante."""


def home_contract(connection, account):
    """El código del contrato a domicilio con el que se despacha, o ``None``."""
    contracts = [contract for contract in account.contracts or [] if contract.get("kind") == "home"]
    preferred = str(checkout.config(connection).get("contract") or "")
    for contract in contracts:
        if str(contract.get("code")) == preferred:
            return contract["code"]
    return contracts[0]["code"] if contracts else None


def dispatch(connection, orders):
    """Crea el envío de cada pedido (en orden) con la cuenta del dueño de la
    tienda. Devuelve ``(shipments, failed)``: los envíos para imprimir (los
    nuevos y los que ya existían) y ``[{"number", "detail"}]`` de los que no
    salieron. ``DispatchError`` si la cuenta no permite despachar nada."""
    account = checkout.account_for(connection)
    if account is None:
        raise DispatchError("Conectá tu cuenta de Andreani en la app (Mis pedidos → Conectar Andreani) para despachar desde la tienda.")
    problems = account_problems(account)
    if problems:
        raise DispatchError("A tu cuenta de Andreani le falta " + ", ".join(problems) + ". Completala en la app.")
    contract = home_contract(connection, account)
    if contract is None:
        raise DispatchError("Tu cuenta de Andreani no tiene un contrato a domicilio. Los envíos a sucursal se hacen desde Mis pedidos.")

    shipments, failed = [], []
    for order in orders:
        number = order.external_number or str(order.pk)
        existing = order.carrier_shipments.exclude(status=CarrierShipment.Status.CANCELLED).select_related("account").first()
        if existing is not None:
            shipments.append(existing)
            continue
        try:
            shipments.append(create_shipment(None, account, order, contract))
        except ShipmentError as exc:
            failed.append({"number": number, "detail": str(exc)})
    return shipments, failed
