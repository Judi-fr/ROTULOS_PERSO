"""Cotización del checkout de VTEX: nuestra tabla de tarifas publicada como
tablas de flete (Logistics API).

El checkout de VTEX no le pregunta el precio a nadie de afuera: cotiza con las
tablas de flete de sus políticas de envío. Así que la tabla de tarifas de la
tienda (``ShippingRate``) se publica como una política de envío por modalidad
(``VtexFreightMixin.push_shipping_rates``) y se vuelve a publicar cada vez que
cambia (``shipping_rates.rates_changed`` encola ``internal/push_shipping_rates``).
La política tiene que estar asociada a un muelle (dock) para que el checkout
la use: eso lo hace el comerciante en su admin, una vez (no tocamos su
logística); lo que falta asociar se informa.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import quote

from django.conf import settings

from ..base import ProviderNotFoundError
from .common import _json, _text, _update_preferences

# Tabla de fletes (Logistics API). VTEX la pide en filas con rango de CP y de
# peso; una política de envío por modalidad (``ShippingRate.option_code``).
SHIPPING_POLICY_PREFIX = "rotulos-"
FREIGHT_INSERT, FREIGHT_DELETE = 1, 3
FREIGHT_BATCH_SIZE = 500
# "Sin tope de peso" y "sin tope de volumen": VTEX no acepta un vacío.
UNBOUNDED_WEIGHT = 100_000_000
UNBOUNDED_VOLUME = 1_000_000_000
FREIGHT_PERMISSION = "Logistics shipping full access"


def policy_id_for(option_code):
    """Id de la política de envío de VTEX para una modalidad nuestra."""
    slug = re.sub(r"[^a-z0-9-]+", "-", _text(option_code).lower()).strip("-") or "standard"
    return f"{SHIPPING_POLICY_PREFIX}{slug}"[:50]


def _weight_units(kilos):
    """Kilos -> la unidad de peso de la cuenta (gramos salvo que se configure
    otra: A CONFIRMAR con una cuenta argentina real)."""
    per_kg = Decimal(str(getattr(settings, "VTEX_FREIGHT_WEIGHT_UNITS_PER_KG", 1000)))
    return int((Decimal(kilos) * per_kg).to_integral_value(rounding=ROUND_HALF_UP))


def _time_cost(rate):
    """El plazo como lo pide VTEX (``DD.HH:MM:SS``). VTEX lo exige, así que una
    tarifa sin días cargados promete ``VTEX_DEFAULT_DELIVERY_DAYS``."""
    days = rate.delivery_days_max if rate.delivery_days_max is not None else rate.delivery_days_min
    if days is None:
        days = getattr(settings, "VTEX_DEFAULT_DELIVERY_DAYS", 5)
    return f"{int(days)}.00:00:00"


def freight_rows(rates):
    """Las filas de la tabla de fletes de VTEX para las tarifas de UNA
    modalidad, con la misma regla que ``shipping_rates.matching_rates``: para
    un CP y un peso, gana la franja de menor techo que lo alcance.

    VTEX no sabe de "la más ajustada": aplica la fila cuyo rango de CP y de
    peso contiene al carrito. Así que acá se arma una tabla sin solapamientos
    que da el mismo resultado: los rangos de CP se parten en tramos donde
    aplican siempre las mismas tarifas, y en cada tramo las franjas pasan a
    ser rangos de peso consecutivos (de 0 al primer techo, de ahí al segundo,
    y lo que no tiene techo arriba de todo). Los tramos vecinos con las
    mismas franjas se vuelven a juntar."""
    items = []
    for rate in rates:
        try:
            start, end = int(rate.postal_code_from), int(rate.postal_code_to)
        except (TypeError, ValueError):
            continue
        ceiling = _weight_units(rate.weight_up_to_kg) if rate.weight_up_to_kg is not None else None
        items.append((start, end, ceiling, rate))
    if not items:
        return []

    bounds = sorted({start for start, *_ in items} | {end + 1 for _, end, *_ in items})
    segments = []
    for low, next_low in zip(bounds, bounds[1:]):
        high = next_low - 1
        covering = [item for item in items if item[0] <= low and item[1] >= high]
        if not covering:
            continue
        # Entre franjas con el mismo techo, la que se cargó primero.
        covering.sort(key=lambda item: (item[2] is None, item[2] or 0, item[3].pk or 0))
        brackets, previous_ceiling = [], None
        for _start, _end, ceiling, rate in covering:
            if brackets and ceiling == previous_ceiling:
                continue
            weight_start = 0 if previous_ceiling is None else previous_ceiling + 1
            brackets.append((weight_start, UNBOUNDED_WEIGHT if ceiling is None else ceiling, rate))
            if ceiling is None:
                break  # Lo que no tiene techo cubre todo lo demás.
            previous_ceiling = ceiling
        key = tuple((low_w, high_w, str(rate.price), _time_cost(rate)) for low_w, high_w, rate in brackets)
        if segments and segments[-1]["key"] == key and segments[-1]["end"] + 1 == low:
            segments[-1]["end"] = high
        else:
            segments.append({"start": low, "end": high, "key": key})

    rows = []
    for segment in segments:
        for weight_start, weight_end, price, time_cost in segment["key"]:
            rows.append(
                {
                    "zipCodeStart": f"{segment['start']:04d}",
                    "zipCodeEnd": f"{segment['end']:04d}",
                    "weightStart": weight_start,
                    "weightEnd": weight_end,
                    "absoluteMoneyCost": f"{Decimal(price):.2f}",
                    "pricePercent": 0,
                    "pricePercentByWeight": 0,
                    "maxVolume": UNBOUNDED_VOLUME,
                    "timeCost": time_cost,
                    "country": "ARG",
                    "polygon": "",
                }
            )
    return rows


def _new_shipping_policy(policy_id, name):
    """Una política de envío con lo mínimo que exige VTEX: sin agenda, sin
    límites de valor ni de medidas (0 = sin límite), sin modal ni retiro."""
    return {
        "id": policy_id,
        "name": name,
        "shippingMethod": name,
        "weekendAndHolidays": {"saturday": False, "sunday": False, "holiday": False},
        "maxDimension": {"largestMeasure": 0.0, "maxMeasureSum": 0.0},
        # A CONFIRMAR: cuántos ítems acepta por envío. Alto para no partir un
        # pedido en varios paquetes.
        "numberOfItemsPerShipment": 1000,
        "minimumValueAceptable": 0.0,
        "maximumValueAceptable": 0.0,
        "deliveryScheduleSettings": {"useDeliverySchedule": False, "dayOfWeekForDelivery": [], "maxRangeDelivery": 0},
        "carrierSchedule": [],
        "cubicWeightSettings": {"volumetricFactor": 0.0, "minimunAcceptableVolumetricWeight": 0.0},
        "modalSettings": {"modals": [], "useOnlyItemsWithDefinedModal": False},
        "businessHourSettings": {"carrierBusinessHours": [], "isOpenOutsideBusinessHours": True},
        "pickupPointsSettings": {"pickupPointIds": [], "pickupPointTags": [], "sellers": []},
        "isActive": True,
    }


class VtexFreightMixin:
    """La parte de ``VtexProvider`` que publica la tabla de tarifas. Usa su
    ``api_request``."""

    def _ensure_shipping_policy(self, connection, policy_id, name):
        """Crea la política de envío si no existe; si existe, solo le
        actualiza el nombre. Nunca la reactiva: si el comerciante la apagó en
        VTEX, es su decisión."""
        path = f"api/logistics/pvt/shipping-policies/{quote(policy_id, safe='')}"
        try:
            current = _json(self.api_request(connection, "GET", path, permission=FREIGHT_PERMISSION))
        except ProviderNotFoundError:
            self.api_request(
                connection,
                "POST",
                "api/logistics/pvt/shipping-policies",
                json_body=_new_shipping_policy(policy_id, name),
                permission=FREIGHT_PERMISSION,
            )
            return
        if isinstance(current, dict) and current.get("name") != name:
            self.api_request(
                connection,
                "PUT",
                path,
                json_body=dict(current, name=name, shippingMethod=name),
                permission=FREIGHT_PERMISSION,
            )

    def _send_freight_rows(self, connection, policy_id, rows, operation):
        path = f"api/logistics/pvt/configuration/freights/{quote(policy_id, safe='')}/values/update"
        for index in range(0, len(rows), FREIGHT_BATCH_SIZE):
            batch = [dict(row, operationType=operation) for row in rows[index : index + FREIGHT_BATCH_SIZE]]
            self.api_request(connection, "POST", path, json_body=batch, permission=FREIGHT_PERMISSION)

    def push_shipping_rates(self, connection, rates):
        """Publica ``rates`` (las tarifas activas; vacío = despublicar) como
        tablas de flete, una política de envío por modalidad.

        La API de VTEX agrega filas, no reemplaza la tabla: para que lo que se
        borró acá se borre allá, se guarda lo último publicado
        (``preferences["vtex_freight"]``) y se manda la diferencia: primero se
        borran las filas que ya no van, después se agregan las nuevas. Se
        guarda política por política, así un corte a mitad de camino deja
        anotado lo que sí llegó. Devuelve ``{"rows", "policies",
        "unlinked_policies"}``: las políticas que todavía no están asociadas a
        ningún muelle no cotizan."""
        by_code = {}
        for rate in rates:
            by_code.setdefault(rate.option_code, []).append(rate)

        published = {key: list(value) for key, value in ((connection.preferences or {}).get("vtex_freight") or {}).items()}
        wanted = {}
        for code, code_rates in sorted(by_code.items()):
            policy_id = policy_id_for(code)
            self._ensure_shipping_policy(connection, policy_id, code_rates[0].option_name[:100])
            wanted[policy_id] = wanted.get(policy_id, []) + freight_rows(code_rates)

        for policy_id in sorted(set(published) | set(wanted)):
            old, new = published.get(policy_id, []), wanted.get(policy_id, [])
            self._send_freight_rows(connection, policy_id, [row for row in old if row not in new], FREIGHT_DELETE)
            self._send_freight_rows(connection, policy_id, [row for row in new if row not in old], FREIGHT_INSERT)
            if new:
                published[policy_id] = new
            else:
                published.pop(policy_id, None)
            _update_preferences(connection, vtex_freight=dict(published))

        docks = _json(self.api_request(connection, "GET", "api/logistics/pvt/configuration/docks", permission=FREIGHT_PERMISSION))
        linked = set()
        for dock in docks if isinstance(docks, list) else []:
            if isinstance(dock, dict):
                linked.update(_text(table) for table in dock.get("freightTableIds") or [])
        return {
            "rows": sum(len(rows) for rows in wanted.values()),
            "policies": sorted(wanted),
            "unlinked_policies": sorted(policy_id for policy_id in wanted if policy_id not in linked),
        }
