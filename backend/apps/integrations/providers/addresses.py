"""Direcciones que llegan en una sola línea ("Av. Corrientes 1234").

El rótulo guarda calle y número por separado (``Address.street``/``number``),
pero Shopify y WooCommerce mandan ambos en un único campo. Tiendanube ya los
manda separados y no usa esto.
"""

from __future__ import annotations

import re

# "Av. Siempre Viva 742" -> ("Av. Siempre Viva", "742"). El número es lo
# último que empieza con dígito ("Calle 12 1500" -> "Calle 12" + "1500",
# como en La Plata). Si no termina en número, todo es la calle.
STREET_NUMBER_RE = re.compile(r"^(?P<street>.*\S)\s+(?P<number>\d+\s?[A-Za-z]?)$")
# Donde el número va ADELANTE ("105 Victoria St", Canadá/EE. UU.) NO se
# separa: el rótulo imprime calle + número en ese orden y saldría
# "Victoria St 105". Tampoco se busca un número al principio en general: en
# Argentina "25 de Mayo" o "9 de Julio" son nombres de calle.


def split_street(address1):
    address1 = (str(address1).strip() if address1 is not None else "").rstrip(", ")
    match = STREET_NUMBER_RE.match(address1)
    if not match:
        return address1, ""
    return match.group("street").strip().rstrip(","), match.group("number").replace(" ", "")
