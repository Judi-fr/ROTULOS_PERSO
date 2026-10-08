"""Firma OAuth 1.0a (HMAC-SHA256) de los pedidos a la API REST de Magento.

Magento 2.4.4+ no acepta por defecto el access token de una Integración como
Bearer (Adobe desaconseja prenderlo), así que cada pedido se firma con las
cuatro credenciales. Se hace con la biblioteca estándar: es poco código y
evita una dependencia.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote


def _encode(value):
    """Percent-encoding de OAuth 1.0a (RFC 3986: solo quedan sin codificar
    letras, dígitos y ``-._~``)."""
    return quote(str(value), safe="-._~")


def oauth1_header(method, url, params, consumer_key, consumer_secret, token, token_secret, *, nonce=None, timestamp=None):
    """El header ``Authorization: OAuth ...`` de un pedido firmado con
    HMAC-SHA256. ``url`` sin query string; ``params`` son los de la query
    (entran en la firma). El cuerpo JSON no se firma (OAuth 1.0a solo firma
    cuerpos de formulario)."""
    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": str(timestamp or int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    pairs = sorted((_encode(key), _encode(value)) for key, value in list((params or {}).items()) + list(oauth_params.items()))
    normalized = "&".join(f"{key}={value}" for key, value in pairs)
    base_string = "&".join((method.upper(), _encode(url), _encode(normalized)))
    signing_key = f"{_encode(consumer_secret)}&{_encode(token_secret)}"
    signature = base64.b64encode(hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha256).digest()).decode()
    oauth_params["oauth_signature"] = signature
    return "OAuth " + ", ".join(f'{key}="{_encode(value)}"' for key, value in sorted(oauth_params.items()))
