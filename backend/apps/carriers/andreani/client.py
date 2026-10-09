"""Cliente de la API de Andreani (Transporte y Distribución).

Escrito el 2026-10-08 SIN credenciales (Andreani solo se las da a clientes, por
su ejecutivo comercial): sale de la documentación oficial, que Andreani publica
como planillas en https://developers.andreani.com/document (orden de envío v2,
etiquetas v2, tracking v3, sucursales v2, cotizador v1 —planilla
``api-cotizador-v2-1.xlsx``—, nueva-acción v2), y está
probado contra una Andreani simulada (``tests/fake_andreani.py``). Lo que hay que
confirmar con la primera cuenta está marcado "A CONFIRMAR".

- **Autenticación:** ``GET /login`` con Basic (usuario/contraseña de la cuenta
  del cliente) devuelve un token que dura 24 horas; se manda en el header
  ``x-authorization-token``. Se guarda cifrado en la cuenta y se renueva un rato
  antes de vencer, o al primer 401.
- **Dos ambientes:** QA (``apisqa``) y producción, elegidos por cuenta.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from urllib.parse import quote

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

TOKEN_HEADER = "x-authorization-token"
# El token dura 24 h: se renueva una hora antes, por relojes desparejos.
TOKEN_LIFETIME = timedelta(hours=23)


class AndreaniError(Exception):
    """Falla hablando con Andreani (red, 5xx): en general se puede reintentar."""


class AndreaniAuthError(AndreaniError):
    """Andreani rechazó las credenciales: reintentar no lo arregla."""


class AndreaniRejectedError(AndreaniError):
    """Andreani rechazó el pedido por inválido (400/404/422); el mensaje es suyo."""


def _setting(name, default):
    return getattr(settings, name, default)


def base_url(environment):
    if environment == "qa":
        return str(_setting("ANDREANI_API_BASE_QA", "https://apisqa.andreani.com")).rstrip("/")
    return str(_setting("ANDREANI_API_BASE_PRODUCTION", "https://apis.andreani.com")).rstrip("/")


def _json(response):
    try:
        return response.json()
    except ValueError:
        return {}


def _error_detail(response):
    """El mensaje de error de Andreani, si vino uno legible."""
    body = _json(response)
    if isinstance(body, dict):
        for key in ("detail", "message", "mensaje", "title", "error"):
            if body.get(key):
                return str(body[key])[:300]
        errors = body.get("errors") or body.get("errores")
        if errors:
            return str(errors)[:300]
    return ""


class AndreaniClient:
    def __init__(self, account, *, timeout=None):
        self.account = account
        self.base = base_url(account.environment)
        # El checkout pasa uno corto: está en medio de la venta de otro.
        self.timeout = timeout or _setting("ANDREANI_HTTP_TIMEOUT_SECONDS", 20)

    # --- Sesión ------------------------------------------------------------

    def login(self):
        """Pide un token nuevo y lo guarda en la cuenta. ``AndreaniAuthError``
        si las credenciales no sirven."""
        if not self.account.username or not self.account.password:
            raise AndreaniAuthError("Falta cargar el usuario y la contraseña de Andreani.")
        try:
            response = requests.get(
                f"{self.base}/login",
                auth=(self.account.username, self.account.password),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AndreaniError(f"No se pudo contactar a Andreani: {exc.__class__.__name__}.") from exc
        if response.status_code in (401, 403):
            raise AndreaniAuthError("Andreani rechazó el usuario o la contraseña.")
        if response.status_code >= 400:
            raise AndreaniError(f"Andreani respondió HTTP {response.status_code} al iniciar sesión.")
        # A CONFIRMAR: la documentación dice que el token viene en el header;
        # por las dudas también se lee del cuerpo.
        body = _json(response)
        token = response.headers.get(TOKEN_HEADER) or (body.get("token") if isinstance(body, dict) else "")
        if not token:
            raise AndreaniError("Andreani no devolvió el token de sesión.")
        self.account.token = token
        self.account.token_expires_at = timezone.now() + TOKEN_LIFETIME
        type(self.account).objects.filter(pk=self.account.pk).update(
            token_encrypted=self.account.token_encrypted, token_expires_at=self.account.token_expires_at
        )
        return token

    def _token(self):
        expires = self.account.token_expires_at
        if self.account.token_encrypted and expires and expires > timezone.now():
            return self.account.token
        return self.login()

    def request(self, method, path, *, params=None, json_body=None, accept="application/json", auth=True, retry=True):
        headers = {"Accept": accept}
        if auth:
            headers[TOKEN_HEADER] = self._token()
        try:
            response = requests.request(
                method,
                f"{self.base}/{path.lstrip('/')}",
                params=params or None,
                json=json_body,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise AndreaniError(f"No se pudo contactar a Andreani: {exc.__class__.__name__}.") from exc
        if response.status_code == 401 and auth and retry:
            # Token vencido antes de tiempo o revocado: uno nuevo y otra vez.
            self.login()
            return self.request(method, path, params=params, json_body=json_body, accept=accept, auth=auth, retry=False)
        label = f"{method} /{path.lstrip('/').split('?', 1)[0]}"
        if response.status_code in (401, 403):
            raise AndreaniAuthError(f"Andreani no autorizó {label}: revisá la cuenta y sus contratos.")
        if response.status_code in (400, 404, 409, 422):
            detail = _error_detail(response)
            suffix = f": {detail}" if detail else f" ({label}, HTTP {response.status_code})."
            raise AndreaniRejectedError("Andreani rechazó el pedido" + suffix)
        if response.status_code >= 400:
            raise AndreaniError(f"Andreani respondió HTTP {response.status_code} en {label}.")
        return response

    # --- Operaciones -------------------------------------------------------

    def create_order(self, payload):
        """``POST /v2/ordenes-de-envio`` -> la respuesta (dict): ``bultos[]``
        con ``numeroDeEnvio``, ``agrupadorDeBultos``, sucursales asignadas."""
        data = _json(self.request("POST", "v2/ordenes-de-envio", json_body=payload))
        if not isinstance(data, dict) or not data.get("bultos"):
            raise AndreaniError("Andreani no devolvió los bultos de la orden.")
        return data

    def label(self, number, *, fmt="pdf", package=None):
        """La etiqueta de Andreani (bytes) de un envío o agrupador: PDF por
        defecto, ZPL con ``fmt="zpl"``."""
        params = {"bulto": package} if package else None
        accept = "application/zpl" if fmt == "zpl" else "application/pdf"
        return self.request("GET", f"v2/ordenes-de-envio/{quote(str(number), safe='')}/etiquetas", params=params, accept=accept).content

    def traces(self, number):
        """``GET /v3/envios/{numero}/trazas`` -> lista de eventos (dicts)."""
        data = _json(self.request("GET", f"v3/envios/{quote(str(number), safe='')}/trazas"))
        events = data.get("eventos") if isinstance(data, dict) else data
        return [event for event in events or [] if isinstance(event, dict)]

    def quote(self, *, contract, client_code, postal_code, packages):
        """``GET /v1/tarifas`` -> lo que costaría el envío (dict): ``pesoAforado``
        y ``tarifaSinIva``/``tarifaConIva`` con ``seguroDistribucion``,
        ``distribucion`` y ``total`` (strings). ``packages``: dicts con
        ``volumen`` (cm³, obligatorio), ``kilos`` y opcionalmente
        ``valorDeclarado``. Los bultos van como ``bultos[0][kilos]``, como pide
        la documentación. A CONFIRMAR: la planilla no dice si el cotizador pide
        token; se manda igual (es la misma cuenta que crea los envíos)."""
        params = {"cpDestino": str(postal_code), "contrato": str(contract), "cliente": str(client_code)}
        for index, package in enumerate(packages):
            for key, value in package.items():
                if value not in (None, ""):
                    params[f"bultos[{index}][{key}]"] = str(value)
        data = _json(self.request("GET", "v1/tarifas", params=params))
        if not isinstance(data, dict) or not isinstance(data.get("tarifaConIva"), dict):
            raise AndreaniError("Andreani no devolvió la tarifa.")
        return data

    def branches(self, postal_code=""):
        """Sucursales y puntos HOP (``GET /v2/sucursales``, no pide token). Con
        ``postal_code``, las que atienden ese código postal."""
        params = {"codigoPostal": postal_code} if postal_code else None
        data = _json(self.request("GET", "v2/sucursales", params=params, auth=False))
        return [branch for branch in (data if isinstance(data, list) else []) if isinstance(branch, dict)]

    def cancel(self, contract, number):
        """``POST /v2/nueva-accion`` con ``accion: cancelacion``. Andreani solo
        la acepta si el envío todavía no empezó a moverse."""
        payload = {"accion": "cancelacion", "datos": {"contrato": contract, "numeroAndreani": [str(number)]}}
        return _json(self.request("POST", "v2/nueva-accion", json_body=payload))
