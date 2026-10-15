"""El precio de los transportistas en el checkout de las tiendas, para el
comerciante (``checkout_transportistas.html``). Se monta en ``/api/v1/carriers/``.

- ``checkout/`` GET: los transportistas que se pueden ofrecer (con el estado de
  la cuenta del usuario y sus contratos a domicilio) y las tiendas activas del
  usuario cuyo checkout nos pregunta el precio, con la configuración de cada
  transportista en cada una. PUT ``{store, carrier, enabled, contract, name,
  delivery_days_min?, delivery_days_max?, surcharge_percent?, surcharge_amount?,
  free_shipping_from?}``.
- ``checkout/test/`` POST ``{store, carrier, postal_code, weight_kg?, cart_total?}``:
  lo que cotizaría, sin caché.

Lo propio de cada transportista lo resuelve ``checkout.checkout_for(code)``.
Todo con ``orders.create`` y siempre sobre las tiendas del usuario.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from rest_framework import status
from rest_framework.response import Response

from apps.audit.services import record
from apps.integrations.models import StoreConnection
from apps.integrations.providers import get_provider as get_store_provider

from .base import ShipmentError
from .checkout import CARRIERS, checkout_for
from .models import CarrierAccount
from .providers import get_provider
from .views import _CarrierView


def checkout_stores(user):
    """Las tiendas activas del usuario cuyo checkout nos pregunta el precio."""
    stores = StoreConnection.objects.filter(owner=user, status=StoreConnection.Status.ACTIVE).order_by("pk")
    return [store for store in stores if getattr(get_store_provider(store.platform), "quotes_at_checkout", False)]


def optional_days(value):
    if value in (None, ""):
        return None
    days = int(value)
    if days < 0 or days > 90:
        raise ValueError
    return days


def optional_money(value, maximum):
    """``None`` si viene vacío; si no, un ``Decimal`` entre 0 y ``maximum``
    como string con dos decimales. ``ValueError`` si no sirve."""
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError from exc
    if not number.is_finite() or number < 0 or number > maximum:
        raise ValueError
    return str(number.quantize(Decimal("0.01")))


def checkout_settings(carrier, store, account):
    values = carrier.config(store)
    return {
        "enabled": bool(values.get("enabled")),
        "contract": values.get("contract") or "",
        "name": values.get("name") or carrier.DEFAULT_NAME,
        "delivery_days_min": values.get("delivery_days_min"),
        "delivery_days_max": values.get("delivery_days_max"),
        "surcharge_percent": values.get("surcharge_percent"),
        "surcharge_amount": values.get("surcharge_amount"),
        "free_shipping_from": values.get("free_shipping_from"),
        "problems": carrier.problems(store, account, values) if values.get("enabled") else [],
    }


def validated_values(data, carrier):
    """La configuración que manda la página, validada -> ``(values, None)`` o
    ``(None, Response 400)``."""
    try:
        days_min = optional_days(data.get("delivery_days_min"))
        days_max = optional_days(data.get("delivery_days_max"))
    except (TypeError, ValueError):
        return None, Response({"delivery_days_min": ["Los días van de 0 a 90."]}, status=status.HTTP_400_BAD_REQUEST)
    if days_min is not None and days_max is not None and days_min > days_max:
        return None, Response({"delivery_days_max": ["El máximo no puede ser menor que el mínimo."]}, status=status.HTTP_400_BAD_REQUEST)
    money_fields = {}
    for name, maximum, message in (
        ("surcharge_percent", Decimal("300"), "El recargo va de 0 a 300 %."),
        ("surcharge_amount", Decimal("10000000"), "El recargo fijo tiene que ser un monto en pesos, cero o más."),
        ("free_shipping_from", Decimal("1000000000"), "El monto para el envío gratis tiene que ser cero o más."),
    ):
        try:
            money_fields[name] = optional_money(data.get(name), maximum)
        except ValueError:
            return None, Response({name: [message]}, status=status.HTTP_400_BAD_REQUEST)
    return {
        "enabled": bool(data.get("enabled")),
        "contract": str(data.get("contract") or "").strip()[:50],
        "name": str(data.get("name") or "").strip()[:100] or carrier.DEFAULT_NAME,
        "delivery_days_min": days_min,
        "delivery_days_max": days_max,
        **money_fields,
    }, None


def save_checkout(request, store, carrier, values):
    """Guarda, avisa a la plataforma si cambió qué opciones se cotizan y lo
    deja en la auditoría."""
    before = carrier.config(store)
    carrier.save_config(store, values)
    if before.get("enabled") != values["enabled"] or before.get("name") != values["name"]:
        # Cambió qué opciones cotizamos en el checkout (Tiendanube necesita
        # una opción del carrier con ese código para mostrar la tarifa).
        get_store_provider(store.platform).checkout_prices_changed(store)
    changes = {
        f"{carrier.PREF}.{key}": {"from": before.get(key), "to": value} for key, value in values.items() if before.get(key) != value
    }
    record(
        request,
        category="integrations",
        action="store.update",
        target=store,
        target_type="storeconnection",
        target_repr=str(store.name or store.pk),
        changes=changes or None,
    )


class CheckoutView(_CarrierView):
    def get(self, request):
        accounts = {account.carrier: account for account in CarrierAccount.objects.filter(owner=request.user, is_active=True)}
        carriers = []
        for carrier in CARRIERS:
            provider = get_provider(carrier.OPTION_CODE)
            account = accounts.get(provider.code)
            carriers.append(
                {
                    "code": provider.code,
                    "name": provider.name,
                    "default_name": carrier.DEFAULT_NAME,
                    "account_exists": account is not None,
                    "account_problems": (
                        [f"a tu cuenta le falta {item}" for item in provider.credential_problems(account, "quote")]
                        + provider.checkout_problems(account)
                    )
                    if account
                    else [],
                    "home_contracts": [item for item in (account.contracts or []) if item.get("kind") == "home"] if account else [],
                }
            )
        stores = []
        for store in checkout_stores(request.user):
            stores.append(
                {
                    "store": store.pk,
                    "store_name": store.name or store.external_store_id,
                    "platform": store.platform,
                    "platform_label": store.get_platform_display(),
                    "carriers": {
                        carrier.OPTION_CODE: checkout_settings(carrier, store, accounts.get(carrier.OPTION_CODE)) for carrier in CARRIERS
                    },
                }
            )
        return Response({"carriers": carriers, "stores": stores})

    def put(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        carrier = checkout_for(data.get("carrier"))
        if carrier is None:
            return Response({"carrier": ["Elegí el transportista."]}, status=status.HTTP_400_BAD_REQUEST)
        store = next((item for item in checkout_stores(request.user) if str(item.pk) == str(data.get("store"))), None)
        if store is None:
            return Response({"store": ["Esa tienda no es tuya o su checkout no nos pregunta el precio."]}, status=status.HTTP_400_BAD_REQUEST)
        values, error = validated_values(data, carrier)
        if error is not None:
            return error
        account = carrier.account_for(store)
        if values["enabled"]:
            found = carrier.problems(store, account, values)
            if found:
                return Response({"detail": "No se puede activar: " + "; ".join(found) + "."}, status=status.HTTP_400_BAD_REQUEST)
        save_checkout(request, store, carrier, values)
        return Response(dict(checkout_settings(carrier, store, account), carrier=carrier.OPTION_CODE, store=store.pk))


class CheckoutTestView(_CarrierView):
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        carrier = checkout_for(data.get("carrier"))
        if carrier is None:
            return Response({"carrier": ["Elegí el transportista."]}, status=status.HTTP_400_BAD_REQUEST)
        store = next((item for item in checkout_stores(request.user) if str(item.pk) == str(data.get("store"))), None)
        if store is None:
            return Response({"store": ["Esa tienda no es tuya o su checkout no nos pregunta el precio."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            weight = Decimal(str(data.get("weight_kg") or 0))
        except (InvalidOperation, ValueError):
            weight = Decimal("0")
        try:
            cart_total = Decimal(str(data["cart_total"])) if data.get("cart_total") not in (None, "") else None
            if cart_total is not None and not cart_total.is_finite():
                cart_total = None
        except (InvalidOperation, ValueError):
            cart_total = None
        try:
            result = carrier.test_quote(store, data.get("postal_code"), weight if weight.is_finite() else Decimal("0"), cart_total)
        except ShipmentError as exc:
            return Response({"ok": False, "detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(dict(result, ok=True))
