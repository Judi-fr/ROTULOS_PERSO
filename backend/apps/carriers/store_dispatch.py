"""Despachar desde el admin de la tienda (acción masiva de Tiendanube, Shopify o
WooCommerce; ver ``apps.integrations.store_print``), con cualquier transportista.

Allá no hay un diálogo donde elegir, así que se decide solo:

- **El transportista:** el que la tienda tiene elegido para esto
  (``preferences["dispatch_carrier"]``, en Mis tiendas → "Rótulos de esta
  tienda"); si no eligió, el único que el dueño tiene listo; con varios
  listos, el único que esa tienda ofrece en su checkout. Si aun así hay más
  de uno, se le pide que elija (no se adivina con qué correo sale un paquete).
- **El servicio:** el contrato a domicilio con el que la tienda cotiza ese
  transportista en el checkout, o el primero a domicilio de la cuenta. A
  sucursal no se puede: alguien tendría que elegir la sucursal de cada
  pedido (eso queda en Mis pedidos).

Un pedido que ya tiene su envío abierto no se vuelve a crear: se imprime el que
tiene, sea del transportista que sea.
"""

from __future__ import annotations

from .base import ShipmentError
from .checkout import checkout_for
from .models import CarrierAccount, CarrierShipment
from .providers import all_providers, get_provider

PREF = "dispatch_carrier"


class DispatchError(Exception):
    """No se puede despachar ninguno; el mensaje es para el comerciante."""


def preferred_code(connection):
    return str((connection.preferences or {}).get(PREF) or "")


def _ready(provider, account):
    return account is not None and account.is_active and not account.last_error and not provider.account_problems(account)


def carrier_for(connection):
    """``(provider, account)`` con los que se despacha desde esta tienda, o
    ``DispatchError`` con lo que tiene que hacer el comerciante."""
    accounts = {account.carrier: account for account in CarrierAccount.objects.filter(owner_id=connection.owner_id)}
    chosen = get_provider(preferred_code(connection))
    if chosen is not None:
        account = accounts.get(chosen.code)
        if account is None:
            raise DispatchError(f"Conectá tu cuenta de {chosen.name} en la app (Mis pedidos) para despachar desde la tienda.")
        if not _ready(chosen, account):
            detail = account.last_error or ", ".join(chosen.account_problems(account)) or "está desactivada"
            raise DispatchError(f"Tu cuenta de {chosen.name} no está lista ({detail}). Completala en la app.")
        return chosen, account

    ready = [(provider, accounts.get(provider.code)) for provider in all_providers() if _ready(provider, accounts.get(provider.code))]
    if len(ready) == 1:
        return ready[0]
    if not ready:
        names = [provider.name for provider in all_providers()]
        incomplete = [provider for provider in all_providers() if provider.code in accounts]
        if incomplete:
            provider = incomplete[0]
            problems = provider.account_problems(accounts[provider.code]) or [accounts[provider.code].last_error or "revisarla"]
            raise DispatchError(f"A tu cuenta de {provider.name} le falta " + ", ".join(problems) + ". Completala en la app.")
        raise DispatchError(
            f"Conectá tu cuenta de {', '.join(names[:-1])} u {names[-1]} en la app (Mis pedidos) para despachar desde la tienda."
        )
    offered = [(provider, account) for provider, account in ready if (checkout_for(provider.code) and checkout_for(provider.code).config(connection).get("enabled"))]
    if len(offered) == 1:
        return offered[0]
    raise DispatchError(
        "Tenés varios transportistas conectados: elegí con cuál se despacha desde esta tienda en la app "
        "(Mis tiendas → Rótulos de esta tienda)."
    )


def home_contract(connection, provider, account):
    """El código del contrato a domicilio con el que se despacha, o ``None``."""
    contracts = [contract for contract in account.contracts or [] if contract.get("kind") == "home"]
    carrier_checkout = checkout_for(provider.code)
    preferred = str((carrier_checkout.config(connection).get("contract") if carrier_checkout else "") or "")
    for contract in contracts:
        if str(contract.get("code")) == preferred:
            return contract["code"]
    return contracts[0]["code"] if contracts else None


def dispatch(connection, orders):
    """Crea el envío de cada pedido (en orden) con la cuenta del dueño de la
    tienda. Devuelve ``(shipments, failed)``: los envíos para imprimir (los
    nuevos y los que ya existían) y ``[{"number", "detail"}]`` de los que no
    salieron. ``DispatchError`` si no se puede despachar nada."""
    provider, account = carrier_for(connection)
    contract = home_contract(connection, provider, account)
    if contract is None:
        raise DispatchError(
            f"Tu cuenta de {provider.name} no tiene un servicio a domicilio. Los envíos a sucursal se hacen desde Mis pedidos."
        )

    shipments, failed = [], []
    for order in orders:
        number = order.external_number or str(order.pk)
        existing = order.carrier_shipments.exclude(status=CarrierShipment.Status.CANCELLED).select_related("account").first()
        if existing is not None:
            shipments.append(existing)
            continue
        try:
            shipments.append(provider.create_shipment(None, account, order, contract))
        except ShipmentError as exc:
            failed.append({"number": number, "detail": str(exc)})
    return shipments, failed
