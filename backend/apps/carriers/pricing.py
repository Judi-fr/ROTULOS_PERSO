"""Lo que paga el comprador por un envío cotizado en el checkout, igual para
todos los transportistas: el costo con el recargo de la tienda, o nada desde el
monto de envío gratis. También el peso con el que se cotiza (redondeado hacia
arriba, por franjas: 1,23 y 1,27 kg cotizan lo mismo y nunca de menos)."""

from __future__ import annotations

from decimal import ROUND_UP, Decimal

WEIGHT_STEP = Decimal("0.5")


def decimal_or_none(value):
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return number if number.is_finite() else None


def rounded_weight(weight_kg, account):
    weight = Decimal(str(weight_kg or 0))
    if weight <= 0:
        # El carrito sin pesos cargados: se cotiza el paquete por defecto de la
        # cuenta, el mismo que se manda al crear el envío.
        weight = Decimal(account.default_weight_kg)
    steps = (weight / WEIGHT_STEP).to_integral_value(rounding=ROUND_UP)
    return max(steps, 1) * WEIGHT_STEP


def buyer_price(cost, values, cart_total=None):
    """Lo que paga el comprador por un envío que a la tienda le cuesta
    ``cost``, con su recargo y su envío gratis."""
    free_from = decimal_or_none(values.get("free_shipping_from"))
    if free_from is not None and cart_total is not None and cart_total >= free_from:
        return Decimal("0.00")
    percent = decimal_or_none(values.get("surcharge_percent")) or Decimal("0")
    amount = decimal_or_none(values.get("surcharge_amount")) or Decimal("0")
    price = cost * (Decimal("1") + percent / Decimal("100")) + amount
    return max(price, Decimal("0")).quantize(Decimal("0.01"))
