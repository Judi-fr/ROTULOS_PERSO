"""Helpers que comparten ``provider.py`` y ``freight.py`` de VTEX."""

from __future__ import annotations


def _text(value):
    return str(value).strip() if value is not None else ""


def _json(response):
    try:
        return response.json()
    except ValueError:
        return {}


def _update_preferences(connection, **values):
    """Escribe en ``preferences`` con un UPDATE y no con ``save()``: el worker
    trae copias de la conexión que pueden ser viejas."""
    preferences = dict(connection.preferences or {})
    if all(preferences.get(key) == value for key, value in values.items()):
        return
    preferences.update(values)
    connection.preferences = preferences
    type(connection).objects.filter(pk=connection.pk).update(preferences=preferences)


def _set_last_error(connection, message):
    if connection.last_error != message:
        connection.last_error = message
        type(connection).objects.filter(pk=connection.pk).update(last_error=message)
