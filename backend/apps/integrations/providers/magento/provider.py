"""Proveedor Magento 2 / Adobe Commerce (REST, https://developer.adobe.com/commerce/webapi/rest/).

Fase 1, escrita sin una tienda Magento a mano (2026-10-08): sale de la
referencia oficial de la API REST y está probada contra un Magento simulado.
Lo que hay que confirmar con la primera tienda real está marcado
"A CONFIRMAR".

Lo que cambia respecto de las otras plataformas:

- **Cada tienda es un sitio propio** (como WooCommerce): no hay tienda de
  apps ni OAuth "instalá la app". El comerciante crea una *Integración* en su
  admin (Sistema → Extensiones → Integraciones), le da los permisos de
  pedidos y nos pega sus cuatro credenciales (consumer key/secret, access
  token/secret; ``magento.views.MagentoManualConnectView``). No vencen. Se guardan
  cifradas en ``access_token`` (JSON).
- **Cada pedido se firma con OAuth 1.0a (HMAC-SHA256)** en vez de mandar el
  access token como Bearer: desde Magento 2.4.4 eso viene apagado por defecto
  y prenderlo es un ajuste de seguridad que Adobe desaconseja. La firma se
  hace con la biblioteca estándar (``oauth.oauth1_header``), sin dependencias.
- **Solo HTTPS**, igual que WooCommerce: la firma prueba quién pide, pero no
  oculta los datos de los compradores.
- **No hay webhooks** en Magento Open Source. Los pedidos entran por el
  repaso (``supports_reconciliation``, cada ``MAGENTO_RECONCILE_MINUTES``),
  que acá es barato y exacto: la API filtra por ``updated_at``. Con nuestro
  módulo instalado (fase 2, ``magento_module/``, ver ``admin_module.py``) la
  tienda además avisa cada pedido guardado al instante
  (``MODULE_ORDER_EVENT``), imprime/despacha desde su grilla de pedidos y
  cotiza en su checkout. El módulo es opcional: sin él todo sigue andando.
- **Despachar crea un envío (shipment)** con el número de seguimiento;
  Magento le manda el email al comprador. Nunca se factura: en Magento
  facturar es cobrar el pago, y eso es del comerciante. "Entregado" no existe
  en Magento, así que no se informa.
- Del comprador solo se guarda lo que necesita el rótulo: ni email ni
  teléfono.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
from datetime import timezone as dt_timezone
from urllib.parse import quote, urlparse

import requests
from django.conf import settings
from django.utils.dateparse import parse_datetime

from ... import safe_http
from ..addresses import split_street
from ..base import (
    NormalizedOrder,
    OrdersPage,
    ProviderAuthError,
    ProviderError,
    ProviderNotFoundError,
    ProviderRejectedError,
    StoreInfo,
    StoreProvider,
    WebhookMessage,
    get_header,
)
from ..woocommerce.provider import AR_STATES, COUNTRY_NAMES
from .oauth import _encode, oauth1_header

logger = logging.getLogger(__name__)

# Ruta de la API REST. Sin reescritura de URLs (un hosting sin mod_rewrite) la
# misma API vive bajo /index.php/rest/: se prueba al conectar y se recuerda
# (``preferences["magento_rest_path"]``).
REST_PATHS = ("/rest/V1/", "/index.php/rest/V1/")

CANCELLED_STATES = ("canceled", "closed")
COMPLETE_STATE = "complete"
SHIPPED_ORDER_STATUSES = ("dispatched", "in_transit", "delivered")
# El aviso que manda nuestro módulo cada vez que se guarda un pedido
# (``Observer/OrderSaved.php``), firmado con el secreto de la tienda.
MODULE_ORDER_EVENT = "rotulos/order_saved"
# Lo que el módulo publica en su API REST para que le escribamos a dónde
# avisar, qué tienda es y con qué firmar (``etc/webapi.xml``).
MODULE_CONFIG_PATH = "rotulos/config"
MODULE_SIGNATURE_HEADER = "X-Rotulos-Signature"
# Los transportistas que no son de Magento se informan como "custom": el
# nombre del correo va en el título del seguimiento.
CUSTOM_CARRIER_CODE = "custom"

SITE_URL_RE = re.compile(r"^[a-z0-9]([a-z0-9\-\.]*[a-z0-9])?(:\d+)?(/[^\s?#]*)?$")


def _text(value):
    return str(value).strip() if value is not None else ""


def _timeout():
    return getattr(settings, "MAGENTO_HTTP_TIMEOUT_SECONDS", 15)


def _json(response):
    try:
        return response.json()
    except ValueError:
        return {}


def _quantity(value):
    """Magento manda las cantidades como decimales (``2.0000``)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1
    return int(number) if number.is_integer() else number


def _magento_datetime(value):
    """ISO -> ``2026-10-08 12:00:00`` en UTC (el formato de los filtros)."""
    parsed = parse_datetime(value) if isinstance(value, str) else value
    if parsed is None:
        return ""
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt_timezone.utc)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def credentials(connection):
    """Las cuatro credenciales de la integración, como dict."""
    try:
        data = json.loads(connection.access_token or "{}")
    except ValueError:
        data = {}
    keys = ("consumer_key", "consumer_secret", "access_token", "access_token_secret")
    values = {key: _text(data.get(key)) for key in keys}
    if not all(values.values()):
        raise ProviderAuthError("La tienda no tiene las credenciales de Magento guardadas: hay que volver a conectarla.")
    return values


def credentials_token(consumer_key, consumer_secret, access_token, access_token_secret):
    """Lo que se guarda en ``access_token`` (cifrado por el modelo)."""
    return json.dumps(
        {
            "consumer_key": consumer_key,
            "consumer_secret": consumer_secret,
            "access_token": access_token,
            "access_token_secret": access_token_secret,
        }
    )


def external_store_id_for(site_url):
    """``https://MiTienda.com/tienda/`` -> ``mitienda.com/tienda``."""
    parsed = urlparse(site_url)
    return f"{(parsed.netloc or '').lower()}{parsed.path.rstrip('/')}"


def search_criteria(filters=(), *, page=1, page_size=50, sort_field="", sort_direction="ASC"):
    """Los parámetros ``searchCriteria[...]`` de una búsqueda REST de Magento.
    ``filters``: ``(campo, valor, condición)``, todos con AND."""
    params = {}
    for index, (field, value, condition) in enumerate(filters):
        prefix = f"searchCriteria[filter_groups][{index}][filters][0]"
        params[f"{prefix}[field]"] = field
        params[f"{prefix}[value]"] = value
        params[f"{prefix}[condition_type]"] = condition
    if sort_field:
        params["searchCriteria[sortOrders][0][field]"] = sort_field
        params["searchCriteria[sortOrders][0][direction]"] = sort_direction
    params["searchCriteria[pageSize]"] = page_size
    params["searchCriteria[currentPage]"] = page
    return params


class MagentoProvider(StoreProvider):
    platform = "magento"
    # Solo con nuestro módulo instalado; sin él todo entra por el repaso, así
    # que los avisos no son obligatorios (``webhooks_required``).
    order_sync_events = (MODULE_ORDER_EVENT,)
    webhooks_required = False
    webhook_secret_per_store = True
    # El método de envío del módulo pregunta el precio en el checkout (rates.py).
    quotes_at_checkout = True
    requires_shop_domain = True
    uses_authorization_code = False
    uses_install_url = False
    supports_reconciliation = True

    @property
    def orders_page_size(self):
        return getattr(settings, "MAGENTO_ORDERS_PAGE_SIZE", 50)

    @property
    def reconcile_minutes(self):
        return getattr(settings, "MAGENTO_RECONCILE_MINUTES", 5)

    # --- Sitio -------------------------------------------------------------

    def normalize_shop_domain(self, value):
        """La URL base de la tienda, ``https://host[/ruta]`` sin barra final.
        Acepta ``mitienda.com`` o una URL pegada de la tienda o de su API.
        ``ValueError`` si no es una URL o es HTTP. La del admin no sirve: su
        ruta la elige cada tienda (``/admin_x1y2``) y no se puede recortar."""
        raw = _text(value)
        if raw.lower().startswith("http://"):
            raise ValueError("La tienda tiene que usar HTTPS (https://...).")
        raw = re.sub(r"^https://", "", raw, flags=re.IGNORECASE)
        raw = raw.split("?", 1)[0].split("#", 1)[0]
        raw = re.split(r"/(?:index\.php/)?rest(?:/|$)|/index\.php(?:/|$)", raw, maxsplit=1)[0].rstrip("/")
        host, _, path = raw.partition("/")
        candidate = f"{host.lower()}{'/' + path if path else ''}"
        if not raw or "." not in host or not SITE_URL_RE.match(candidate):
            raise ValueError("Ingresá la dirección de tu tienda (por ejemplo, https://mitienda.com).")
        return f"https://{candidate}"

    # --- API ---------------------------------------------------------------

    def _send(self, site_url, creds, method, path, params, json_body, rest_path):
        url = f"{site_url.rstrip('/')}{rest_path}{path.lstrip('/')}"
        params = {key: str(value) for key, value in (params or {}).items()}
        header = oauth1_header(
            method, url, params, creds["consumer_key"], creds["consumer_secret"], creds["access_token"], creds["access_token_secret"]
        )
        # La query se arma acá, con la misma codificación que se firmó: si la
        # armara requests, un corchete codificado distinto rompe la firma.
        query = "&".join(f"{_encode(key)}={_encode(value)}" for key, value in params.items())
        try:
            return safe_http.request(
                method,
                f"{url}?{query}" if query else url,
                json=json_body,
                headers={"Authorization": header, "Accept": "application/json", "Content-Type": "application/json"},
                timeout=_timeout(),
            )
        except requests.RequestException as exc:
            raise ProviderError(f"No se pudo contactar a la tienda: {exc.__class__.__name__}.") from exc

    def request_with_credentials(self, site_url, creds, method, path, *, params=None, json_body=None, rest_path=REST_PATHS[0]):
        return self._check(self._send(site_url, creds, method, path, params, json_body, rest_path), method, path)

    def api_request(self, connection, method, path, *, params=None, json_body=None):
        rest_path = (connection.preferences or {}).get("magento_rest_path") or REST_PATHS[0]
        return self.request_with_credentials(
            connection.store_url, credentials(connection), method, path, params=params, json_body=json_body, rest_path=rest_path
        )

    def _check(self, response, method, path):
        label = f"{method} /{path.lstrip('/').split('?', 1)[0]}"
        body = _json(response)
        detail = body.get("message") if isinstance(body, dict) else ""
        if response.status_code in (401, 403):
            raise ProviderAuthError(
                f"Magento rechazó las credenciales (HTTP {response.status_code}): puede que hayan borrado la "
                "integración, que le falte un permiso (Ventas → Operaciones → Pedidos) o que estén mal copiadas."
                + (f" Magento dice: {str(detail)[:200]}" if detail else "")
            )
        if response.status_code == 404:
            raise ProviderNotFoundError(f"Magento: no existe {label} (HTTP 404).")
        if response.status_code in (400, 422):
            raise ProviderRejectedError(
                f"Magento rechazó {label} (HTTP {response.status_code})" + (f": {str(detail)[:300]}" if detail else ".")
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderError(f"La tienda respondió HTTP {response.status_code} en {label}.")
        if response.status_code >= 400:
            raise ProviderRejectedError(f"Magento rechazó {label} (HTTP {response.status_code}).")
        return response

    def check_credentials(self, site_url, creds):
        """Valida las credenciales pegadas a mano antes de guardarlas: tienen
        que poder leer pedidos. Devuelve la ruta de la API que respondió
        (con o sin ``index.php``). ``ProviderAuthError`` si Magento las
        rechaza; ``ProviderNotFoundError`` si ahí no hay un Magento."""
        params = search_criteria(page_size=1)
        for rest_path in REST_PATHS:
            try:
                self.request_with_credentials(site_url, creds, "GET", "orders", params=params, rest_path=rest_path)
            except ProviderNotFoundError:
                continue
            return rest_path
        raise ProviderNotFoundError(
            "No encontramos la API de Magento en esa dirección. Revisá que sea la de tu tienda (no la del admin)."
        )

    def get_store_info(self, connection):
        """El nombre visible de la tienda no está en la API con los permisos
        que pedimos: la tienda se llama como su dirección."""
        return StoreInfo(name=external_store_id_for(connection.store_url), store_url=connection.store_url)

    # --- Pedidos -----------------------------------------------------------

    def get_order(self, connection, order_id):
        data = _json(self.api_request(connection, "GET", f"orders/{quote(str(order_id), safe='')}"))
        if not isinstance(data, dict) or data.get("entity_id") in (None, ""):
            raise ProviderError("Magento devolvió el pedido vacío o con un formato inesperado.")
        return data

    def _orders_page(self, connection, field, since, cursor, per_page):
        """Una página de pedidos con ``field >= since``, en orden. La lista de
        Magento ya trae cada pedido completo (direcciones incluidas). Pasada la
        última página, Magento repite la última en vez de devolver vacío: el
        corte sale de ``total_count``."""
        page = max(int(cursor or 1), 1)
        filters = [(field, _magento_datetime(since), "gteq")] if since else []
        params = search_criteria(filters, page=page, page_size=per_page, sort_field=field)
        data = _json(self.api_request(connection, "GET", "orders", params=params))
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ProviderError("Magento devolvió la lista de pedidos con un formato inesperado.")
        try:
            total = int(data.get("total_count") or 0)
        except (TypeError, ValueError):
            total = 0
        orders = [order for order in data["items"] if isinstance(order, dict)]
        has_next = page * per_page < total
        return OrdersPage(orders=orders, next_cursor=str(page + 1) if has_next else "")

    def list_orders_page(self, connection, *, created_at_min="", cursor="", per_page=50):
        return self._orders_page(connection, "created_at", created_at_min, cursor, per_page)

    def list_updated_orders_page(self, connection, *, updated_after, cursor="", per_page=50):
        return self._orders_page(connection, "updated_at", updated_after, cursor, per_page)

    @staticmethod
    def _shipping_address(raw):
        """La dirección de envío (en ``shipping_assignments``); un pedido sin
        envío (virtual, descargable) no la tiene y se usa la de facturación."""
        for assignment in (raw.get("extension_attributes") or {}).get("shipping_assignments") or []:
            address = ((assignment or {}).get("shipping") or {}).get("address")
            if isinstance(address, dict) and address.get("street"):
                return address
        return raw.get("billing_address") or {}

    def normalize_order(self, raw):
        if not isinstance(raw, dict) or raw.get("entity_id") in (None, ""):
            raise ValueError("El pedido de Magento no trae 'entity_id'.")

        address = self._shipping_address(raw)
        lines = [_text(line) for line in address.get("street") or [] if _text(line)]
        street, number = split_street(lines[0] if lines else "")
        country_code = _text(address.get("country_id")).upper()
        region = _text(address.get("region"))
        region_code = _text(address.get("region_code"))
        state = region or region_code
        if country_code == "AR" and not region and region_code.upper() in AR_STATES:
            state = AR_STATES[region_code.upper()]

        items = []
        for item in raw.get("items") or []:
            # Un producto configurable viene dos veces (el padre y su
            # variante): solo cuenta el padre.
            if isinstance(item, dict) and not item.get("parent_item_id"):
                items.append({"name": _text(item.get("name")), "sku": _text(item.get("sku")), "quantity": _quantity(item.get("qty_ordered"))})

        updated = _text(raw.get("updated_at"))
        recipient = " ".join(part for part in (_text(address.get("firstname")), _text(address.get("lastname"))) if part)
        return NormalizedOrder(
            external_id=_text(raw.get("entity_id")),
            # El número que ve el comerciante en el admin y el comprador en
            # sus emails ("000000123").
            external_number=_text(raw.get("increment_id")) or _text(raw.get("entity_id")),
            recipient_name=recipient,
            street=street,
            number=number,
            city=_text(address.get("city")),
            state=state,
            postal_code=_text(address.get("postcode")),
            country=COUNTRY_NAMES.get(country_code, country_code) or "Argentina",
            reference=", ".join(lines[1:]),
            description=", ".join(f"{item['quantity']}x {item['name']}" for item in items if item["name"]),
            shipping_option=_text(raw.get("shipping_description")),
            status=self._local_status(raw),
            items=items,
            # Magento guarda las fechas en UTC, sin zona en el texto.
            external_updated_at=parse_datetime(f"{updated}+00:00".replace(" ", "T")) if updated else None,
            raw=self._stored_copy(raw, address, items),
        )

    @staticmethod
    def _stored_copy(raw, address, items):
        """Lo que se guarda: lo que hace falta para rotular y despachar. Ni
        email, ni teléfono, ni pagos."""
        return {
            "entity_id": raw.get("entity_id"),
            "increment_id": raw.get("increment_id"),
            "state": raw.get("state"),
            "status": raw.get("status"),
            "created_at": raw.get("created_at"),
            "updated_at": raw.get("updated_at"),
            "shipping_description": raw.get("shipping_description"),
            "shipping_address": {key: value for key, value in address.items() if key not in ("email", "telephone", "fax")},
            "items": items,
        }

    @staticmethod
    def _local_status(raw):
        state = _text(raw.get("state")).lower()
        if state in CANCELLED_STATES:
            return "cancelled"
        # "complete" = enviado (y facturado) por completo.
        if state == COMPLETE_STATE:
            return "dispatched"
        return ""

    # --- Despacho y seguimiento -------------------------------------------

    def fulfillment_status_for(self, order_status):
        return "shipped" if order_status in SHIPPED_ORDER_STATUSES else None

    def _shipments(self, connection, order_id):
        params = search_criteria([("order_id", str(order_id), "eq")], page_size=50)
        data = _json(self.api_request(connection, "GET", "shipments", params=params))
        items = data.get("items") if isinstance(data, dict) else None
        return [shipment for shipment in items or [] if isinstance(shipment, dict)]

    @staticmethod
    def tracking_comment(tracking_code, tracking_url, carrier=""):
        by = f" por {carrier}" if carrier else ""
        return f"Tu pedido fue despachado{by}. Número de seguimiento: {tracking_code}. Seguí tu envío en: {tracking_url}"

    def push_fulfillment(
        self, connection, order_id, *, status, tracking_code="", tracking_url="", carrier="", notify_customer=True
    ):
        """Despacha en Magento: si el pedido no tiene envío, lo crea (con el
        seguimiento, avisando al comprador); si ya tiene uno (el comerciante
        despachó desde su admin), le agrega el seguimiento que falte. Magento
        no tiene dónde guardar la URL de seguimiento de un correo "custom":
        va en un comentario visible para el comprador. Idempotente: no repite
        un número ni un comentario que ya están."""
        order = self.get_order(connection, order_id)
        if _text(order.get("state")).lower() in CANCELLED_STATES:
            return []

        done = []
        title = carrier or "Envío"
        shipments = self._shipments(connection, order_id)
        if not shipments:
            body = {"notify": bool(notify_customer), "appendComment": False}
            if tracking_code:
                body["tracks"] = [{"track_number": tracking_code, "title": title, "carrier_code": CUSTOM_CARRIER_CODE}]
            self.api_request(connection, "POST", f"order/{quote(str(order_id), safe='')}/ship", json_body=body)
            done.append("shipment")
        elif tracking_code:
            tracks = [track for shipment in shipments for track in shipment.get("tracks") or [] if isinstance(track, dict)]
            if not any(_text(track.get("track_number")) == tracking_code for track in tracks):
                shipment = shipments[0]
                self.api_request(
                    connection,
                    "POST",
                    "shipment/track",
                    json_body={
                        "entity": {
                            "order_id": int(order_id),
                            "parent_id": shipment.get("entity_id"),
                            "track_number": tracking_code,
                            "title": title,
                            "carrier_code": CUSTOM_CARRIER_CODE,
                        }
                    },
                )
                if notify_customer:
                    # A CONFIRMAR: que el email de envío reenviado muestre el
                    # seguimiento nuevo.
                    self.api_request(connection, "POST", f"shipment/{shipment.get('entity_id')}/emails")
                done.append("tracking")

        if tracking_code and tracking_url:
            comments = [_text(history.get("comment")) for history in order.get("status_histories") or [] if isinstance(history, dict)]
            if not any(tracking_url in comment for comment in comments):
                self.api_request(
                    connection,
                    "POST",
                    f"orders/{quote(str(order_id), safe='')}/comments",
                    json_body={
                        "statusHistory": {
                            "comment": self.tracking_comment(tracking_code, tracking_url, carrier),
                            "is_customer_notified": 1 if notify_customer else 0,
                            "is_visible_on_front": 1,
                            "parent_id": int(order_id),
                        }
                    },
                )
                done.append("comment")
        return done

    # --- Nuestro módulo (fase 2) ---------------------------------------------

    def register_webhooks(self, connection, url, events):
        """Magento no tiene webhooks que registrar por API: los avisos los manda
        nuestro módulo, al que ``configure_admin_print`` le escribe la URL. Acá
        solo se asegura el secreto con el que los va a firmar."""
        self.ensure_webhook_secret(connection)
        return []

    def configure_admin_print(self, connection, base_url):
        """Le escribe a nuestro módulo (``POST /V1/rotulos/config``, su propia
        API, con las credenciales de la Integración) a dónde avisar los
        pedidos, pedir la impresión y la cotización, qué tienda es y el
        secreto con el que firmar. Sin el módulo esa ruta no existe (404): no
        es un error, la tienda funciona igual. Si la Integración no tiene el
        permiso del módulo (401/403) tampoco queda vinculado."""
        from django.urls import reverse

        body = {
            "storeId": str(connection.pk),
            "secret": self.ensure_webhook_secret(connection),
            "webhookUrl": f"{base_url}{reverse('magento-webhooks')}?store={connection.pk}",
            "printLinkUrl": f"{base_url}{reverse('magento-print-link')}",
            "ratesUrl": f"{base_url}{reverse('magento-rates')}",
        }
        try:
            response = self.api_request(connection, "POST", MODULE_CONFIG_PATH, json_body=body)
        except ProviderNotFoundError:
            _remember_module(connection, False)
            return False
        except ProviderAuthError as exc:
            logger.warning("La Integración de la tienda %s no tiene permiso para el módulo: %s", connection.pk, exc)
            _remember_module(connection, False)
            return False
        except ProviderError as exc:
            # Una falla pasajera no dice nada del módulo: se deja lo que había.
            logger.warning("No se pudo configurar el módulo de la tienda %s: %s", connection.pk, exc)
            return False
        version = _json(response)
        _remember_module(connection, True, version if isinstance(version, str) else "")
        return True

    def verify_webhook(self, raw_body, headers, connection=None):
        secret = connection.webhook_secret if connection is not None else ""
        signature = get_header(headers, MODULE_SIGNATURE_HEADER).strip()
        if not secret or not signature:
            return False
        digest = hmac.new(secret.encode("utf-8"), raw_body or b"", hashlib.sha256).digest()
        return hmac.compare_digest(signature.encode("utf-8"), base64.b64encode(digest))

    def parse_webhook(self, raw_body, headers):
        """El aviso del módulo: ``{"event": "order_saved", "order_id",
        "updated_at"}``. ``{"event": "ping"}`` (el botón "Probar conexión" del
        módulo) se contesta sin encolar nada. En la cola queda solo el id: el
        worker vuelve a pedir el pedido."""
        try:
            payload = json.loads((raw_body or b"").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("El aviso debe ser JSON válido.") from None
        if not isinstance(payload, dict):
            raise ValueError("El aviso debe ser un objeto.")
        event = _text(payload.get("event"))
        if event == "ping":
            return None
        if event != "order_saved":
            raise ValueError(f"Aviso desconocido: {event or '(vacío)'}.")
        order_id = _text(payload.get("order_id"))
        if not order_id.isdigit():
            raise ValueError("El aviso no trae el id del pedido.")
        return WebhookMessage(
            store_id="",
            event_type=MODULE_ORDER_EVENT,
            resource_id=order_id,
            payload={"id": order_id, "updated_at": _text(payload.get("updated_at"))},
        )


def _remember_module(connection, linked, version=""):
    """``preferences["print_plugin"]`` (la misma clave que el plugin de
    WooCommerce: ``tiendas.html`` la muestra igual) y la versión del módulo.
    Con un UPDATE y no ``save()``: el worker trae copias viejas de la tienda."""
    preferences = dict(connection.preferences or {}, print_plugin=linked)
    if version:
        preferences["magento_module_version"] = version[:20]
    if preferences != (connection.preferences or {}):
        connection.preferences = preferences
        type(connection).objects.filter(pk=connection.pk).update(preferences=preferences)
