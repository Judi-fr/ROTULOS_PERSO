"""Proveedor Empretienda: una tienda SIN API.

Empretienda no tiene API ni webhooks (2026-10: ni pública ni para partners;
DUX, que la integra, también importa a mano). Lo único que da es la planilla
de "Gestión de ventas → Listado de ventas → Exportar". Así que este proveedor
no habla con nadie: existe para que la tienda sea una ``StoreConnection`` como
las demás (sus pedidos agrupados, con su remitente, logo, plantilla y filtro
propio, idempotentes por número de orden) y los pedidos entran importando esa
planilla (``spreadsheet.py``).

No hay credenciales, ni webhooks, ni importación automática, ni devolución del
despacho: el seguimiento se carga en el admin de Empretienda.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ..base import StoreInfo, StoreProvider

SITE_URL_RE = re.compile(r"^[a-z0-9]([a-z0-9\-\.]*[a-z0-9])?(:\d+)?$")


def external_store_id_for(store_url):
    """``https://MiTienda.empretienda.com.ar/`` -> ``mitienda.empretienda.com.ar``."""
    return (urlparse(store_url).netloc or "").lower()


class EmpretiendaProvider(StoreProvider):
    platform = "empretienda"
    order_sync_events = ()
    # Sin API: no hay pedidos que traer solos ni a dónde mandar a autorizar.
    supports_order_import = False
    requires_shop_domain = True
    uses_authorization_code = False
    uses_install_url = False

    def normalize_shop_domain(self, value):
        """La dirección de la tienda, ``https://host``: la de Empretienda
        (``mitienda.empretienda.com.ar``) o un dominio propio. Sirve para
        identificarla (dos cuentas no pueden cargar la misma tienda), así que
        solo cuenta el dominio. ``ValueError`` si no es una dirección."""
        raw = str(value or "").strip().lower()
        raw = re.sub(r"^https?://", "", raw).split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if not raw or "." not in raw or not SITE_URL_RE.match(raw):
            raise ValueError("Ingresá la dirección de tu tienda (por ejemplo, https://mitienda.empretienda.com.ar).")
        return f"https://{raw}"

    def get_store_info(self, connection):
        return StoreInfo(name=connection.name or connection.external_store_id, store_url=connection.store_url)

    def register_webhooks(self, connection, url, events):
        return []

    def verify_webhook(self, raw_body, headers):
        return False

    def parse_webhook(self, raw_body, headers):
        raise ValueError("Empretienda no manda avisos: los pedidos entran importando su planilla de ventas.")
