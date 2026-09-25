"""Filtros de rango de fechas por query string, compartidos por los listados.

``?date_from=``/``?date_to=`` en formato AAAA-MM-DD, **ambos inclusive**, es
parte del contrato de la API en seis apps distintas (pedidos, rótulos,
documentos, auditoría, soporte y usuarios). Hasta ahora cada una traía su
propia copia del parseo: siete funciones, cinco de ellas idénticas carácter
por carácter, con dos mensajes de error distintos entre sí.

La comparación se hace siempre con ``__date`` sobre un ``DateTimeField``: sin
eso, ``date_to=2026-09-24`` excluiría todo lo de ese mismo día salvo la
medianoche exacta, que no es lo que espera quien filtra "hasta hoy".
"""

from __future__ import annotations

from datetime import datetime

from django.db.models import Q
from rest_framework.exceptions import ValidationError

DATE_FORMAT = "%Y-%m-%d"


def parse_date_param(raw, param_name):
    """La fecha de un query param, o ``None`` si no vino.

    Un formato inválido es un 400 (``ValidationError``) y no un 500: el
    valor lo escribe quien llama a la API, así que es un error suyo.
    ``TypeError`` se captura junto con ``ValueError`` porque el valor puede
    no ser texto cuando viene de un body JSON y no de la query string.
    """
    raw = (raw or "").strip() if isinstance(raw, str) else raw
    if not raw:
        return None
    try:
        return datetime.strptime(raw, DATE_FORMAT).date()
    except (TypeError, ValueError):
        raise ValidationError(
            {param_name: f"Formato de fecha inválido (usar YYYY-MM-DD): {raw!r}."}
        )


def parse_date_range(query_params, from_param="date_from", to_param="date_to"):
    """``(desde, hasta)`` ya parseados y en orden coherente.

    Un rango al revés se rechaza acá y no más adelante: un queryset vacío
    no distingue "no hay nada" de "escribiste las fechas al revés".
    """
    date_from = parse_date_param(query_params.get(from_param), from_param)
    date_to = parse_date_param(query_params.get(to_param), to_param)
    if date_from and date_to and date_from > date_to:
        raise ValidationError(
            {to_param: f"'{to_param}' no puede ser anterior a '{from_param}'."}
        )
    return date_from, date_to


def date_range_q(query_params, field="created_at", from_param="date_from", to_param="date_to"):
    """El ``Q`` del rango sobre ``field``, listo para ``.filter()``.

    Sin ningún parámetro devuelve un ``Q()`` vacío, que al filtrar no hace
    nada: el llamador no necesita preguntar si vinieron las fechas.
    """
    date_from, date_to = parse_date_range(query_params, from_param, to_param)
    condition = Q()
    if date_from:
        condition &= Q(**{f"{field}__date__gte": date_from})
    if date_to:
        condition &= Q(**{f"{field}__date__lte": date_to})
    return condition
