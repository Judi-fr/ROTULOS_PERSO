"""Cifrado de credenciales de terceros guardadas en la base (hoy: el access
token OAuth de cada tienda conectada, ver ``models.StoreConnection``).

Los tokens de Tiendanube no vencen: quien lea la base (un backup filtrado,
un dump para soporte) podría operar la tienda del cliente. Por eso nunca se
guardan en texto plano, sino cifrados con Fernet (AES-CBC + HMAC) usando
``INTEGRATIONS_ENCRYPTION_KEY``.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    key = getattr(settings, "INTEGRATIONS_ENCRYPTION_KEY", "")
    if not key:
        # Sin clave propia (desarrollo/tests) se deriva una de SECRET_KEY. En
        # producción conviene una clave aparte: si se rota SECRET_KEY, los
        # tokens cifrados con la clave derivada dejan de poder leerse.
        digest = hashlib.sha256(f"integrations:{settings.SECRET_KEY}".encode("utf-8")).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt(value):
    """Texto plano -> token Fernet (str). Vacío queda vacío."""
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(token):
    """Token Fernet -> texto plano. ``ValueError`` si no se puede descifrar
    (clave cambiada o dato corrupto), nunca devuelve basura."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "No se pudo descifrar la credencial guardada (¿cambió INTEGRATIONS_ENCRYPTION_KEY?)."
        ) from exc
