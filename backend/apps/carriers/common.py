"""Lo que todos los transportistas necesitan de un pedido y de una cuenta, en un
solo lugar: el código postal comparable, la provincia como código, el peso y las
medidas de los bultos, y los textos recortados."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

# Códigos de provincia de Argentina (ISO 3166-2:AR): los mismos que piden
# Correo Argentino (Paq.ar y MiCorreo) y que lleva la primera letra de un CPA.
PROVINCES = {
    "A": "Salta",
    "B": "Buenos Aires",
    "C": "Ciudad Autónoma de Buenos Aires",
    "D": "San Luis",
    "E": "Entre Ríos",
    "F": "La Rioja",
    "G": "Santiago del Estero",
    "H": "Chaco",
    "J": "San Juan",
    "K": "Catamarca",
    "L": "La Pampa",
    "M": "Mendoza",
    "N": "Misiones",
    "P": "Formosa",
    "Q": "Neuquén",
    "R": "Río Negro",
    "S": "Santa Fe",
    "T": "Tucumán",
    "U": "Chubut",
    "V": "Tierra del Fuego",
    "W": "Corrientes",
    "X": "Córdoba",
    "Y": "Jujuy",
    "Z": "Santa Cruz",
}

# Cómo la escribe la gente (sin tildes, en minúscula) -> código.
_PROVINCE_ALIASES = {
    "caba": "C",
    "capital": "C",
    "capital federal": "C",
    "ciudad de buenos aires": "C",
    "ciudad autonoma de buenos aires": "C",
    "ciudad autonoma buenos aires": "C",
    "provincia de buenos aires": "B",
    "bs as": "B",
    "bsas": "B",
    "pba": "B",
    "gba": "B",
    "tierra del fuego antartida e islas del atlantico sur": "V",
}


def _plain(value):
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", " ", text.lower()).split()


def text(value, limit=40):
    return str(value or "").strip()[:limit]


def postal_code_digits(value):
    """Los 4 dígitos del CP argentino (un CPA ``C1602ABC`` da ``1602``)."""
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:4]


def province_code(state, postal_code=""):
    """El código de provincia (``"B"``, ``"C"``...) de lo que escribió el
    comprador, o ``""`` si no se reconoce. Un CPA (``C1602ABC``) lo trae en la
    primera letra."""
    words = _plain(state)
    joined = " ".join(words)
    if joined:
        if joined in _PROVINCE_ALIASES:
            return _PROVINCE_ALIASES[joined]
        if len(joined) == 1 and joined.upper() in PROVINCES:
            return joined.upper()
        for code, name in PROVINCES.items():
            if joined == " ".join(_plain(name)):
                return code
        if joined.startswith("provincia de "):
            return province_code(joined[len("provincia de ") :])
    cpa = str(postal_code or "").strip().upper()
    if len(cpa) == 8 and cpa[0] in PROVINCES and cpa[1:5].isdigit():
        return cpa[0]
    return ""


def province_name(code):
    return PROVINCES.get(str(code or "").upper(), "")


def order_number(order):
    return order.external_number or str(order.pk)


def order_weight_kg(order, account):
    """El peso del pedido, o el paquete por defecto de la cuenta si no lo dice."""
    try:
        weight = Decimal(order.total_weight_kg) if order.total_weight_kg else Decimal(account.default_weight_kg)
    except (InvalidOperation, TypeError):
        weight = Decimal(account.default_weight_kg)
    return max(weight, Decimal("0.001"))


def split_packages(order, account, package_count):
    """``(cantidad, kilos por bulto)``: el peso repartido en partes iguales."""
    count = max(int(package_count or 1), 1)
    return count, round(order_weight_kg(order, account) / count, 3)


def box_cm(account):
    """Las medidas de la caja por defecto de la cuenta: ``(largo, ancho, alto)``."""
    return (
        max(int(account.default_length_cm or 1), 1),
        max(int(account.default_width_cm or 1), 1),
        max(int(account.default_height_cm or 1), 1),
    )


def decimal_or_none(value):
    try:
        number = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None
