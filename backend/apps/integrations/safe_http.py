"""Pedidos HTTP a una dirección que escribió el comerciante (WooCommerce,
Magento: cada tienda está en su propio servidor).

Sin control, esa dirección podría apuntar a la red interna del servidor
(``https://10.0.0.5``, ``https://169.254.169.254``, un dominio que resuelve a
127.0.0.1): el servidor le pegaría y el mensaje de error devolvería parte de
la respuesta (SSRF). Acá se resuelve el host y se rechaza cualquier dirección
que no sea pública, también en cada redirección, que se sigue a mano.

``INTEGRATIONS_ALLOW_PRIVATE_HOSTS`` lo apaga (por defecto, solo con
``DEBUG``): la tienda de prueba local se publica por Tailscale y resuelve a
una dirección 100.x de la red privada.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import requests
from django.conf import settings

from .providers.base import ProviderRejectedError

MAX_REDIRECTS = 3
REDIRECT_CODES = (301, 302, 303, 307, 308)
INTERNAL_MESSAGE = "La dirección de la tienda apunta a una red interna: no se puede conectar."


def check_public_url(url):
    """``ProviderRejectedError`` si el host de ``url`` resuelve a una dirección
    que no es pública (privada, loopback, link-local, CGNAT, reservada...)."""
    if getattr(settings, "INTEGRATIONS_ALLOW_PRIVATE_HOSTS", False):
        return
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        raise ProviderRejectedError("La dirección de la tienda no es válida.")
    try:
        infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ProviderRejectedError("No se encontró la dirección de la tienda.") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        if not address.is_global:
            raise ProviderRejectedError(INTERNAL_MESSAGE)


def get(url, **kwargs):
    """``requests.get`` con el mismo control que ``request``."""
    return _follow(lambda method, target, **kw: requests.get(target, **kw), "GET", url, kwargs)


def request(method, url, **kwargs):
    """``requests.request`` que valida el destino y cada redirección.

    Si una redirección cambia de host, no viajan las credenciales (``auth``
    ni ``Authorization``). 301/302/303 pasan a GET sin cuerpo, como hacen los
    navegadores; 307/308 conservan método y cuerpo."""
    return _follow(requests.request, method, url, kwargs)


def _follow(send, method, url, kwargs):
    kwargs["allow_redirects"] = False
    origin = urlsplit(url).hostname
    for _ in range(MAX_REDIRECTS + 1):
        check_public_url(url)
        response = send(method, url, **kwargs)
        location = response.headers.get("Location") if response.status_code in REDIRECT_CODES else None
        if not location:
            return response
        url = urljoin(url, location)
        if response.status_code in (301, 302, 303):
            method = "GET"
            kwargs.pop("json", None)
            kwargs.pop("data", None)
        if urlsplit(url).hostname != origin:
            kwargs.pop("auth", None)
            headers = {k: v for k, v in (kwargs.get("headers") or {}).items() if k.lower() != "authorization"}
            kwargs["headers"] = headers
    raise ProviderRejectedError("La tienda redirige demasiadas veces.")
