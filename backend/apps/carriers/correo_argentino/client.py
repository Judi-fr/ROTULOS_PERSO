"""Clientes de las dos APIs de Correo Argentino, decidido 2026-10-14 usar las dos:

- **Paq.ar** (``/paqar/v1``, manual "Plataforma de Integración – Api 2.0",
  abril 2023): despacha. Credenciales = id de acuerdo (``agreement``, en
  ``CarrierAccount.client_code``) + API key (``password``), en los headers
  ``Authorization: Apikey <key>`` y ``agreement``. Alta de orden, rótulo PDF,
  historial, cancelación y sucursales. NO cotiza.
- **MiCorreo** (``/micorreo/v1``, manual "API MiCorreo", 2025): solo para
  cotizar (``/rates``). Usuario y clave de SU API (Basic) dan un JWT, y se cotiza
  con el ``customerId`` de la cuenta MiCorreo del cliente.

Escrito SIN credenciales (Correo Argentino las da por su área comercial):
probado contra un Correo simulado (``tests/fake_correo.py``). Lo dudoso dice
"A CONFIRMAR"; lo más importante: el manual de Paq.ar no muestra la respuesta del
alta (solo dice que trae el número de seguimiento) y su ejemplo de alta no
coincide con su esquema (con y sin el objeto ``order``): se usa el esquema.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta
from urllib.parse import quote

import requests
from django.conf import settings
from django.utils import timezone

from ..base import CarrierAuthError, CarrierError, CarrierRejectedError

logger = logging.getLogger(__name__)

# El JWT de MiCorreo dura "un par de horas" (trae ``expires``); se renueva antes.
TOKEN_MARGIN = timedelta(minutes=5)


class CorreoError(CarrierError):
    """Falla hablando con Correo Argentino (red, 5xx)."""


class CorreoAuthError(CorreoError, CarrierAuthError):
    """Correo Argentino rechazó las credenciales."""


class CorreoRejectedError(CorreoError, CarrierRejectedError):
    """Correo Argentino rechazó el pedido; el mensaje es suyo."""


def _setting(name, default):
    return getattr(settings, name, default)


def _json(response):
    try:
        return response.json()
    except ValueError:
        return {}


def _detail(response):
    body = _json(response)
    if isinstance(body, dict):
        for key in ("message", "mensaje", "error", "detail"):
            if body.get(key):
                return str(body[key])[:300]
    return ""


def _check(response, label, api):
    if response.status_code in (401, 403):
        detail = _detail(response)
        raise CorreoAuthError(f"{api} rechazó las credenciales" + (f": {detail}" if detail else "."))
    if response.status_code in (400, 402, 404, 409, 422):
        detail = _detail(response)
        raise CorreoRejectedError(f"{api} rechazó el pedido" + (f": {detail}" if detail else f" ({label}, HTTP {response.status_code})."))
    if response.status_code >= 400:
        raise CorreoError(f"{api} respondió HTTP {response.status_code} en {label}.")
    return response


class PaqarClient:
    API = "Correo Argentino"

    def __init__(self, account, *, timeout=None):
        self.account = account
        key = "CORREO_ARGENTINO_PAQAR_BASE_QA" if account.environment == "qa" else "CORREO_ARGENTINO_PAQAR_BASE_PRODUCTION"
        default = "https://apitest.correoargentino.com.ar/paqar/v1" if account.environment == "qa" else "https://api.correoargentino.com.ar/paqar/v1"
        self.base = str(_setting(key, default)).rstrip("/")
        self.timeout = timeout or _setting("CORREO_ARGENTINO_HTTP_TIMEOUT_SECONDS", 20)

    def request(self, method, path, *, params=None, json_body=None):
        if not self.account.client_code or not self.account.password:
            raise CorreoAuthError("Falta cargar el acuerdo y la API key de Paq.ar.")
        headers = {
            "Authorization": f"Apikey {self.account.password}",
            "agreement": str(self.account.client_code),
            "Accept": "application/json",
        }
        try:
            response = requests.request(
                method, f"{self.base}/{path.lstrip('/')}", params=params or None, json=json_body, headers=headers, timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise CorreoError(f"No se pudo contactar a Correo Argentino: {exc.__class__.__name__}.") from exc
        return _check(response, f"{method} /{path.lstrip('/')}", self.API)

    def check_credentials(self):
        """``GET /auth``: 204 si el acuerdo y la API key sirven."""
        self.request("GET", "auth")

    def create_order(self, payload):
        """``POST /orders`` -> la respuesta. A CONFIRMAR su forma: el manual
        solo dice que trae el número de seguimiento (TN)."""
        data = _json(self.request("POST", "orders", json_body=payload))
        return data if isinstance(data, dict) else {"response": data}

    def labels(self, numbers, *, label_format="10x15"):
        """``POST /labels`` -> ``{numero: bytes del PDF}``. ``labelFormat`` va
        como parámetro (A CONFIRMAR: el manual no dice si en la query)."""
        body = [{"sellerId": "", "trackingNumber": str(number)} for number in numbers]
        data = _json(self.request("POST", "labels", params={"labelFormat": label_format}, json_body=body))
        result = {}
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, dict):
                continue
            number = str(item.get("trackingNumber") or "")
            status = str(item.get("result") or item.get("status") or "")
            if status.upper().startswith("ERROR") or not item.get("fileBase64"):
                raise CorreoRejectedError(f"Correo Argentino no dio el rótulo de {number or 'un envío'}: {status or 'sin archivo'}.")
            try:
                result[number] = base64.b64decode(item["fileBase64"])
            except (ValueError, TypeError) as exc:
                raise CorreoError(f"Correo Argentino devolvió un rótulo ilegible para {number}.") from exc
        return result

    def tracking(self, numbers):
        """``GET /tracking`` con la lista de números en el cuerpo (así lo pide
        el manual) -> lista de ``{trackingNumber, event: [...]}``."""
        body = [{"trackingNumber": str(number)} for number in numbers]
        data = _json(self.request("GET", "tracking", json_body=body))
        return [item for item in (data if isinstance(data, list) else [data]) if isinstance(item, dict)]

    def cancel(self, number):
        """``PATCH /orders/{tn}/cancel``: solo antes de que lo imponga."""
        return _json(self.request("PATCH", f"orders/{quote(str(number), safe='')}/cancel"))

    def agencies(self, state_code=""):
        """``GET /agencies`` de una provincia, las que entregan al comprador."""
        params = {"pickup_availability": "true"}
        if state_code:
            # A CONFIRMAR: "ISO 3166-2" puede querer ``AR-B`` en vez de ``B``.
            params["stateId"] = state_code
        data = _json(self.request("GET", "agencies", params=params))
        return [item for item in (data if isinstance(data, list) else []) if isinstance(item, dict)]


class MiCorreoClient:
    API = "MiCorreo"

    def __init__(self, account, *, timeout=None):
        self.account = account
        key = "CORREO_ARGENTINO_MICORREO_BASE_QA" if account.environment == "qa" else "CORREO_ARGENTINO_MICORREO_BASE_PRODUCTION"
        default = (
            "https://apitest.correoargentino.com.ar/micorreo/v1"
            if account.environment == "qa"
            else "https://api.correoargentino.com.ar/micorreo/v1"
        )
        self.base = str(_setting(key, default)).rstrip("/")
        self.timeout = timeout or _setting("CORREO_ARGENTINO_HTTP_TIMEOUT_SECONDS", 20)

    def login(self):
        """``POST /token`` con Basic -> JWT, guardado cifrado en la cuenta."""
        user = (self.account.extra or {}).get("micorreo_user") or ""
        password = self.account.secret("micorreo_password")
        if not user or not password:
            raise CorreoAuthError("Falta cargar el usuario y la clave de la API de MiCorreo.")
        try:
            response = requests.post(f"{self.base}/token", auth=(user, password), timeout=self.timeout)
        except requests.RequestException as exc:
            raise CorreoError(f"No se pudo contactar a MiCorreo: {exc.__class__.__name__}.") from exc
        data = _json(_check(response, "POST /token", self.API))
        token = data.get("token") if isinstance(data, dict) else ""
        if not token:
            raise CorreoError("MiCorreo no devolvió el token.")
        expires = None
        try:
            expires = datetime.strptime(str(data.get("expires") or ""), "%Y-%m-%d %H:%M:%S")
            expires = timezone.make_aware(expires, timezone.get_default_timezone())
        except ValueError:
            expires = timezone.now() + timedelta(hours=1)
        self.account.token = token
        self.account.token_expires_at = expires - TOKEN_MARGIN
        type(self.account).objects.filter(pk=self.account.pk).update(
            token_encrypted=self.account.token_encrypted, token_expires_at=self.account.token_expires_at
        )
        return token

    def _token(self):
        expires = self.account.token_expires_at
        if self.account.token_encrypted and expires and expires > timezone.now():
            return self.account.token
        return self.login()

    def rates(self, payload, *, retry=True):
        """``POST /rates`` -> la lista ``rates`` (``deliveredType``,
        ``productType``, ``price``, ``deliveryTimeMin/Max``)."""
        try:
            response = requests.post(
                f"{self.base}/rates",
                json=payload,
                headers={"Authorization": f"Bearer {self._token()}", "Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise CorreoError(f"No se pudo contactar a MiCorreo: {exc.__class__.__name__}.") from exc
        if response.status_code == 401 and retry:
            self.login()
            return self.rates(payload, retry=False)
        data = _json(_check(response, "POST /rates", self.API))
        rates = data.get("rates") if isinstance(data, dict) else None
        return [rate for rate in rates or [] if isinstance(rate, dict)]
