"""Proveedor VTEX (Orders API, https://developers.vtex.com/docs/api-reference/orders-api).

Escrito sin una cuenta VTEX a mano (2026-10-08): todo sale de la referencia
oficial de la API (su OpenAPI) y está probado contra una VTEX simulada. Lo
que hay que confirmar con la primera cuenta real está marcado "A CONFIRMAR".

Lo que cambia respecto de las otras plataformas:

- **Las credenciales son un appKey + appToken** que el comerciante crea en su
  admin (Configuración de la cuenta → Claves de aplicación) y pega en
  ``tiendas.html`` (``vtex.views.VtexManualConnectView``). No hay OAuth para una
  integración externa como la nuestra, ni vencen. Se guardan cifradas en
  ``access_token`` (JSON ``{"app_key", "app_token"}``). La tienda se
  identifica por el **nombre de la cuenta** (``micuenta`` de
  ``micuenta.myvtex.com``), que es también el host de la API.
- **La clave necesita un rol armado a mano** (no hay rol predefinido que
  incluya el hook): recursos de OMS "List Orders", "View order", "Feed v3 and
  Hook Admin", "Notify invoice" y "Change order workflow status".
- **Los avisos llegan por el "hook" de pedidos**: VTEX POSTea
  ``{"OrderId", "State", "Origin": {"Account"}}`` a la URL que configuramos,
  con los headers que elegimos: ahí va un secreto POR TIENDA
  (``webhook_secret_per_store``), porque el hook no viene firmado. Al
  configurarlo VTEX manda un ping (``{"hookConfig": "ping"}``) que tiene que
  recibir 200, o no guarda la configuración. Cada appKey tiene UN solo hook:
  si la clave ya avisa a otro sistema (un ERP), no se pisa (ver
  ``register_webhooks``).
- **El respaldo es el feed de pedidos**, no una consulta por fecha: la lista
  de pedidos de VTEX no filtra por "modificado desde" y VTEX desaconseja usarla
  para integraciones. El feed es una cola que VTEX guarda hasta 14 días y se
  vacía confirmando lo leído; se lee en cada repaso
  (``supports_reconciliation``, cada ``VTEX_RECONCILE_MINUTES``). No necesita
  URL pública, así que sin hook (desarrollo, o la clave ya usada por otro
  sistema) los pedidos igual entran.
- **El seguimiento va en la factura.** En VTEX un pedido se despacha cuando
  se factura, y la factura en Argentina es fiscal: la emite el sistema de
  facturación del comerciante, nunca nosotros (decisión del 2026-10-08).
  Despachar agrega el número y la URL de seguimiento a las facturas que el
  pedido ya tiene; si todavía no tiene, el aviso se reintenta y, cuando la
  factura aparece (llega por el hook o el feed), se vuelve a mandar
  (``needs_fulfillment_push``). "Entregado" se informa como evento de
  seguimiento con ``isDelivered``.
- Del comprador solo se guarda lo que necesita el rótulo: ni email, ni
  teléfono, ni documento (``clientProfileData`` no se copia).
- La cotización del checkout (publicar la tabla de tarifas) vive en
  ``freight.py``; la conexión manual, en ``views.py``.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
from datetime import timezone as dt_timezone
from urllib.parse import quote, urlencode

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

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
from ..woocommerce.provider import AR_STATES
from .common import _json, _set_last_error, _text, _update_preferences
from .freight import VtexFreightMixin

logger = logging.getLogger(__name__)

APP_KEY_HEADER = "X-VTEX-API-AppKey"
APP_TOKEN_HEADER = "X-VTEX-API-AppToken"
# Header que configuramos en el hook con el secreto de la tienda. "key" es el
# nombre que usa el esquema de la API de VTEX para sus headers.
HOOK_SECRET_HEADER = "key"

# El aviso del hook (y los ítems del feed) dicen "este pedido cambió": el
# worker vuelve a pedir el pedido completo (``handlers.sync_order``).
ORDER_HOOK_EVENT = "order/status_changed"

# Estados del flujo de VTEX que nos importan: desde que se puede preparar
# (pago aprobado y fuera de la ventana de cancelación) hasta facturado o
# cancelado. Antes de "ready-for-handling" el pedido todavía puede caerse y no
# hay nada que rotular.
WORKFLOW_STATUSES = (
    "ready-for-handling",
    "start-handling",
    "handling",
    "invoice",
    "invoiced",
    "cancellation-requested",
    "cancel",
    "canceled",
)
# Los que se traen en la importación inicial (filtro de la lista de pedidos).
IMPORT_STATUSES = ("ready-for-handling", "handling", "invoiced")
CANCELLED_STATUSES = ("canceled", "cancel")
SHIPPED_ORDER_STATUSES = ("dispatched", "in_transit", "delivered")

# Feed: VTEX devuelve hasta 10 ítems por lectura. Un ítem leído y no
# confirmado vuelve a aparecer pasado ``visibilityTimeoutInSeconds``.
FEED_MAX_LOT = 10
FEED_QUEUE = {"visibilityTimeoutInSeconds": 240, "messageRetentionPeriodInSeconds": 1209600}
# La lista de pedidos de VTEX no pasa de 30 páginas ni de 100 por página.
LIST_MAX_PAGES = 30
LIST_MAX_PER_PAGE = 100

COUNTRY_NAMES = {"ARG": "Argentina", "AR": "Argentina"}
ACCOUNT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
VTEX_HOST_SUFFIXES = (".myvtex.com", ".vtexcommercestable.com.br", ".vtexcommercebeta.com.br", ".vtexlocal.com.br")

FOREIGN_HOOK_ERROR = (
    "Esta clave de VTEX ya avisa los pedidos a otro sistema, así que no la cambiamos: los pedidos "
    "entran igual, cada {minutes} minutos. Para que entren al instante, creá una clave solo para "
    "nosotros y volvé a conectar la tienda."
)


def _timeout():
    return getattr(settings, "VTEX_HTTP_TIMEOUT_SECONDS", 15)


def _iso_z(value):
    """Fecha -> ``2026-10-08T12:00:00.000Z`` (el formato de los filtros de VTEX)."""
    return value.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def credentials(connection):
    """``(app_key, app_token)`` guardados de la tienda."""
    try:
        data = json.loads(connection.access_token or "{}")
    except ValueError:
        data = {}
    app_key, app_token = _text(data.get("app_key")), _text(data.get("app_token"))
    if not app_key or not app_token:
        raise ProviderAuthError("La tienda no tiene la clave de VTEX guardada: hay que volver a conectarla.")
    return app_key, app_token


def credentials_token(app_key, app_token):
    """Lo que se guarda en ``access_token`` (cifrado por el modelo)."""
    return json.dumps({"app_key": app_key, "app_token": app_token})


def store_url_for(account):
    return f"https://{account}.myvtex.com"


def output_invoices(raw):
    """Facturas de venta del pedido (las de devolución son ``type: Input``)."""
    packages = (raw.get("packageAttachment") or {}).get("packages") or []
    return [
        package
        for package in packages
        if isinstance(package, dict)
        and _text(package.get("invoiceNumber"))
        and _text(package.get("type") or "Output").lower() == "output"
    ]


class VtexProvider(VtexFreightMixin, StoreProvider):
    platform = "vtex"
    order_sync_events = (ORDER_HOOK_EVENT,)
    requires_shop_domain = True
    uses_authorization_code = False
    uses_install_url = False
    webhook_secret_per_store = True
    supports_reconciliation = True
    supports_rates_push = True  # ver freight.py

    @property
    def orders_page_size(self):
        return min(getattr(settings, "VTEX_ORDERS_PAGE_SIZE", 50), LIST_MAX_PER_PAGE)

    @property
    def reconcile_minutes(self):
        return getattr(settings, "VTEX_RECONCILE_MINUTES", 5)

    # --- Cuenta ------------------------------------------------------------

    def normalize_shop_domain(self, value):
        """El nombre de la cuenta VTEX. Acepta ``micuenta``, la dirección del
        admin (``https://micuenta.myvtex.com/admin``) o un workspace
        (``prueba--micuenta.myvtex.com``). ``ValueError`` si no es una cuenta:
        un dominio propio (``www.mitienda.com.ar``) no dice cuál es."""
        raw = _text(value).lower()
        raw = re.sub(r"^https?://", "", raw)
        host = raw.split("/", 1)[0].split("?", 1)[0].split(":", 1)[0]
        for suffix in VTEX_HOST_SUFFIXES:
            if host.endswith(suffix):
                host = host[: -len(suffix)]
                break
        host = host.split("--")[-1]
        if not host or "." in host or not ACCOUNT_RE.match(host):
            raise ValueError(
                "Escribí el nombre de tu cuenta VTEX: es lo que va antes de .myvtex.com en la dirección "
                "de tu admin (por ejemplo, micuenta)."
            )
        return host

    def api_base_url(self, account):
        template = getattr(settings, "VTEX_API_HOST_TEMPLATE", "https://{account}.vtexcommercestable.com.br")
        return template.format(account=account).rstrip("/")

    # --- API ---------------------------------------------------------------

    def request_with_keys(self, account, app_key, app_token, method, path, *, params=None, json_body=None, permission=""):
        """Pedido a la API con una clave explícita (sin conexión guardada
        todavía, p. ej. al validar la que pegó el comerciante).
        ``permission``: el recurso del rol que hace falta, para el mensaje de
        un 403."""
        url = f"{self.api_base_url(account)}/{path.lstrip('/')}"
        try:
            response = requests.request(
                method,
                url,
                params=params or None,
                json=json_body,
                headers={
                    APP_KEY_HEADER: app_key,
                    APP_TOKEN_HEADER: app_token,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=_timeout(),
            )
        except requests.RequestException as exc:
            raise ProviderError(f"No se pudo contactar a VTEX: {exc.__class__.__name__}.") from exc
        return self._check(response, method, path, permission)

    def api_request(self, connection, method, path, *, params=None, json_body=None, permission=""):
        app_key, app_token = credentials(connection)
        return self.request_with_keys(
            connection.external_store_id,
            app_key,
            app_token,
            method,
            path,
            params=params,
            json_body=json_body,
            permission=permission,
        )

    def _check(self, response, method, path, permission=""):
        label = f"{method} /{path.lstrip('/')}"
        if response.status_code == 401:
            raise ProviderAuthError(
                "VTEX rechazó la clave (HTTP 401): puede que la hayan borrado o que el appKey o el appToken "
                "estén mal copiados."
            )
        if response.status_code == 403:
            needed = f" Agregale al rol de la clave el recurso «{permission}»." if permission else ""
            raise ProviderAuthError(f"La clave de VTEX no tiene permiso para {label} (HTTP 403).{needed}")
        if response.status_code == 404:
            raise ProviderNotFoundError(f"VTEX: no existe {label} (HTTP 404).")
        if response.status_code in (400, 409, 422):
            body = _json(response)
            detail = ""
            if isinstance(body, dict):
                error = body.get("error")
                detail = error.get("message") if isinstance(error, dict) else body.get("message") or error or ""
            raise ProviderRejectedError(
                f"VTEX rechazó {label} (HTTP {response.status_code})" + (f": {str(detail)[:300]}" if detail else ".")
            )
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderError(f"VTEX respondió HTTP {response.status_code} en {label}.")
        if response.status_code >= 400:
            raise ProviderRejectedError(f"VTEX rechazó {label} (HTTP {response.status_code}).")
        return response

    def check_credentials(self, account, app_key, app_token):
        """Valida una clave pegada a mano antes de guardarla: tiene que poder
        listar pedidos. Devuelve avisos (lista de textos) por los permisos que
        no son imprescindibles pero faltan. ``ProviderAuthError`` si VTEX la
        rechaza; ``ProviderNotFoundError`` si la cuenta no existe."""
        try:
            self.request_with_keys(
                account, app_key, app_token, "GET", "api/oms/pvt/orders", params={"per_page": 1}, permission="List Orders"
            )
        except ProviderNotFoundError:
            raise ProviderNotFoundError(
                "No encontramos esa cuenta de VTEX. Revisá el nombre (lo que va antes de .myvtex.com)."
            ) from None

        warnings = []
        try:
            self.request_with_keys(account, app_key, app_token, "GET", "api/orders/feed/config")
        except ProviderNotFoundError:
            pass  # Todavía no hay feed: lo configuramos nosotros.
        except ProviderAuthError:
            warnings.append(
                "La clave no tiene el recurso «Feed v3 and Hook Admin»: sin él no podemos enterarnos de los "
                "pedidos nuevos. Agregáselo al rol de la clave en VTEX."
            )
        except ProviderError:
            pass  # Una falla pasajera no dice nada del permiso.
        return warnings

    def get_store_info(self, connection):
        """VTEX no expone el nombre comercial con los permisos que pedimos: la
        tienda se llama como su cuenta (el comerciante la reconoce)."""
        account = connection.external_store_id
        return StoreInfo(name=account, store_url=store_url_for(account))

    # --- Pedidos -----------------------------------------------------------

    def get_order(self, connection, order_id):
        data = _json(
            self.api_request(connection, "GET", f"api/oms/pvt/orders/{quote(str(order_id), safe='')}", permission="View order")
        )
        if not isinstance(data, dict) or not data.get("orderId"):
            raise ProviderError("VTEX devolvió el pedido vacío o con un formato inesperado.")
        return data

    def list_orders_page(self, connection, *, created_at_min="", cursor="", per_page=50):
        """Importación inicial: los pedidos listos para preparar, en
        preparación o facturados creados desde ``created_at_min``. La lista
        solo trae un resumen (sin dirección), así que cada pedido se vuelve a
        pedir completo. VTEX corta en 30 páginas: con 50 por página son 1500
        pedidos, de sobra para los últimos 30 días de casi cualquier tienda."""
        page = max(int(cursor or 1), 1)
        per_page = min(per_page, LIST_MAX_PER_PAGE)
        params = {
            "page": page,
            "per_page": per_page,
            "orderBy": "creationDate,asc",
            "f_status": ",".join(IMPORT_STATUSES),
        }
        since = parse_datetime(created_at_min) if created_at_min else None
        if since is not None:
            params["f_creationDate"] = f"creationDate:[{_iso_z(since)} TO {_iso_z(timezone.now())}]"
        data = _json(self.api_request(connection, "GET", "api/oms/pvt/orders", params=params, permission="List Orders"))
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise ProviderError("VTEX devolvió la lista de pedidos con un formato inesperado.")

        orders = []
        for summary in data["list"]:
            order_id = _text(summary.get("orderId")) if isinstance(summary, dict) else ""
            if order_id:
                orders.append(self.get_order(connection, order_id))
        try:
            pages = int((data.get("paging") or {}).get("pages") or 0)
        except (TypeError, ValueError):
            pages = 0
        has_next = page < min(pages, LIST_MAX_PAGES) if pages else len(data["list"]) >= per_page
        return OrdersPage(orders=orders, next_cursor=str(page + 1) if has_next and page < LIST_MAX_PAGES else "")

    def list_updated_orders_page(self, connection, *, updated_after, cursor="", per_page=50):
        """Una lectura del feed de pedidos (el repaso). ``updated_after`` no
        se usa: el feed ya es "lo que cambió y no confirmamos". El cursor
        lleva los ítems de la lectura ANTERIOR, que se confirman recién ahora,
        cuando el handler ya guardó sus pedidos: si algo falla en el medio,
        VTEX los vuelve a entregar."""
        handles = json.loads(cursor) if cursor else []
        if handles:
            try:
                self.api_request(
                    connection, "POST", "api/orders/feed", json_body={"handles": handles}, permission="Feed v3 and Hook Admin"
                )
            except (ProviderNotFoundError, ProviderRejectedError) as exc:
                # Ítems vencidos o un feed reconfigurado: VTEX los descarta solo.
                logger.warning("No se pudieron confirmar ítems del feed de la tienda %s: %s", connection.pk, exc)

        try:
            response = self.api_request(
                connection, "GET", "api/orders/feed", params={"maxlot": FEED_MAX_LOT}, permission="Feed v3 and Hook Admin"
            )
        except ProviderNotFoundError:
            # Sin feed configurado (nunca se configuró o VTEX lo borró tras
            # 14 días sin eventos): se crea, y trae lo que cambie desde ahora.
            self.ensure_feed(connection)
            return OrdersPage(orders=[], next_cursor="")
        items = [item for item in _json(response) or [] if isinstance(item, dict)]

        orders, seen = [], set()
        for item in items:
            order_id = _text(item.get("orderId"))
            if not order_id or order_id in seen:
                continue
            seen.add(order_id)
            try:
                orders.append(self.get_order(connection, order_id))
            except ProviderNotFoundError:
                logger.warning("El feed de la tienda %s nombra un pedido que no existe: %s", connection.pk, order_id)
        next_handles = [_text(item.get("handle")) for item in items if _text(item.get("handle"))]
        return OrdersPage(orders=orders, next_cursor=json.dumps(next_handles) if next_handles else "")

    def normalize_order(self, raw):
        if not isinstance(raw, dict) or not _text(raw.get("orderId")):
            raise ValueError("El pedido de VTEX no trae 'orderId'.")

        shipping = raw.get("shippingData") or {}
        address = shipping.get("address") or {}
        logistics = [info for info in shipping.get("logisticsInfo") or [] if isinstance(info, dict)]

        street, number = _text(address.get("street")), _text(address.get("number"))
        if not number:
            street, number = split_street(street)

        city = _text(address.get("city"))
        neighborhood = _text(address.get("neighborhood"))
        reference_parts = [_text(address.get("complement")), _text(address.get("reference"))]
        # El barrio va a la referencia, salvo que repita la localidad (muchas
        # tiendas argentinas cargan ahí la misma ciudad).
        if neighborhood and neighborhood.lower() != city.lower():
            reference_parts.append(neighborhood)

        country_code = _text(address.get("country")).upper()
        state = _text(address.get("state"))
        if country_code in ("ARG", "AR") and state.upper() in AR_STATES:
            state = AR_STATES[state.upper()]

        items = []
        for item in raw.get("items") or []:
            if isinstance(item, dict):
                items.append({"name": _text(item.get("name")), "sku": _text(item.get("refId")), "quantity": item.get("quantity") or 1})

        last_change = _text(raw.get("lastChange"))
        order_id = _text(raw.get("orderId"))
        return NormalizedOrder(
            external_id=order_id,
            # Lo que el comerciante ve en el admin de VTEX es el orderId
            # (``1172452900788-01``), no el ``sequence``.
            external_number=order_id,
            recipient_name=_text(address.get("receiverName")),
            street=street,
            number=number,
            city=city,
            state=state,
            postal_code=_text(address.get("postalCode")),
            country=COUNTRY_NAMES.get(country_code, country_code) or "Argentina",
            reference=", ".join(part for part in reference_parts if part),
            description=", ".join(f"{item['quantity']}x {item['name']}" for item in items if item["name"]),
            shipping_option=_text(logistics[0].get("selectedSla")) if logistics else "",
            status=self._local_status(raw),
            items=items,
            external_updated_at=parse_datetime(last_change) if last_change else None,
            raw=self._stored_copy(raw),
        )

    @staticmethod
    def _stored_copy(raw):
        """Lo que se guarda del pedido: lo que hace falta para rotularlo y
        despacharlo. Nada de ``clientProfileData`` (email, teléfono,
        documento), pagos ni ``contactInformation``."""
        shipping = raw.get("shippingData") or {}
        logistics = [info for info in shipping.get("logisticsInfo") or [] if isinstance(info, dict)]
        package_keys = ("type", "invoiceNumber", "courier", "trackingNumber", "trackingUrl", "issuanceDate", "courierStatus")
        packages = [
            {key: package.get(key) for key in package_keys if key in package}
            for package in (raw.get("packageAttachment") or {}).get("packages") or []
            if isinstance(package, dict)
        ]
        return {
            "orderId": raw.get("orderId"),
            "sequence": raw.get("sequence"),
            "status": raw.get("status"),
            "creationDate": raw.get("creationDate"),
            "lastChange": raw.get("lastChange"),
            "shippingData": {
                "address": dict(shipping.get("address") or {}),
                "logisticsInfo": [
                    {key: info.get(key) for key in ("selectedSla", "deliveryCompany", "deliveryChannel")} for info in logistics
                ],
            },
            "packageAttachment": {"packages": packages},
        }

    @staticmethod
    def _local_status(raw):
        status = _text(raw.get("status")).lower()
        if status in CANCELLED_STATUSES:
            return "cancelled"
        invoices = output_invoices(raw)
        if any((invoice.get("courierStatus") or {}).get("finished") for invoice in invoices):
            return "delivered"
        # Facturado no alcanza: el ERP suele facturar ANTES de que el paquete
        # salga. Con número de seguimiento, sí salió.
        if status == "invoiced" and any(_text(invoice.get("trackingNumber")) for invoice in invoices):
            return "dispatched"
        return ""

    # --- Despacho y seguimiento -------------------------------------------

    def fulfillment_status_for(self, order_status):
        if order_status == "delivered":
            return "delivered"
        return "dispatched" if order_status in SHIPPED_ORDER_STATUSES else None

    def needs_fulfillment_push(self, normalized, order):
        """``True`` si el pedido ya se despachó acá con un seguimiento que
        VTEX no tiene, y ahora sí tiene dónde ponerlo (la factura llegó
        después del despacho). Lo consulta el handler al sincronizar."""
        tracking = _text(order.tracking_number)
        if not tracking or order.status not in SHIPPED_ORDER_STATUSES:
            return False
        invoices = output_invoices(normalized.raw or {})
        return any(_text(invoice.get("trackingNumber")) != tracking for invoice in invoices)

    def push_fulfillment(
        self, connection, order_id, *, status, tracking_code="", tracking_url="", carrier="", notify_customer=True
    ):
        """Informa el despacho: el pedido pasa a "en preparación" si estaba
        listo para preparar, cada factura de venta recibe el número y la URL
        de seguimiento (VTEX le avisa al comprador por email) y, entregado, un
        evento de seguimiento con ``isDelivered``. Nunca crea una factura: sin
        factura, ``ProviderError`` y el aviso se reintenta. Idempotente: no
        repite un seguimiento ni una entrega que VTEX ya tiene."""
        order = self.get_order(connection, order_id)
        current = _text(order.get("status")).lower()
        if current in CANCELLED_STATUSES:
            return []

        done = []
        encoded_id = quote(str(order_id), safe="")
        if current == "ready-for-handling":
            self.api_request(
                connection,
                "POST",
                f"api/oms/pvt/orders/{encoded_id}/start-handling",
                permission="Change order workflow status",
            )
            done.append("start-handling")

        if not tracking_code and status != "delivered":
            return done
        invoices = output_invoices(order)
        if not invoices:
            raise ProviderError(
                "El pedido todavía no tiene factura en VTEX: el seguimiento se manda cuando tu sistema de "
                "facturación la cargue."
            )

        now = timezone.now()
        for invoice in invoices:
            invoice_path = f"api/oms/pvt/orders/{encoded_id}/invoice/{quote(_text(invoice['invoiceNumber']), safe='')}"
            if tracking_code and _text(invoice.get("trackingNumber")) != tracking_code:
                self.api_request(
                    connection,
                    "PATCH",
                    invoice_path,
                    json_body={
                        "trackingNumber": tracking_code,
                        "trackingUrl": tracking_url or None,
                        "courier": carrier or _text(invoice.get("courier")) or None,
                        "dispatchedDate": now.isoformat(),
                    },
                    permission="Notify invoice",
                )
                invoice["trackingNumber"] = tracking_code
                done.append("tracking")
            if status == "delivered" and _text(invoice.get("trackingNumber")):
                if (invoice.get("courierStatus") or {}).get("finished"):
                    continue
                address = (order.get("shippingData") or {}).get("address") or {}
                self.api_request(
                    connection,
                    "PUT",
                    f"{invoice_path}/tracking",
                    json_body={
                        "isDelivered": True,
                        "deliveredDate": now.isoformat(),
                        "events": [
                            {
                                "city": _text(address.get("city")),
                                "state": _text(address.get("state")),
                                "description": "Entregado",
                                "date": now.date().isoformat(),
                            }
                        ],
                    },
                    permission="Notify invoice",
                )
                done.append("delivered")
        return done

    # --- Hook y feed -------------------------------------------------------

    def ensure_webhook_secret(self, connection):
        """Igual que en WooCommerce: el secreto se crea una sola vez, con la
        fila bloqueada, para que una copia vieja de la conexión no invente
        otro distinto del que ya tiene configurado el hook."""
        if not connection.webhook_secret:
            from ...models import StoreConnection

            with transaction.atomic():
                fresh = StoreConnection.objects.select_for_update().get(pk=connection.pk)
                if not fresh.webhook_secret:
                    fresh.webhook_secret = secrets.token_urlsafe(32)
                    fresh.save(update_fields=["webhook_secret_encrypted", "updated_at"])
            connection.webhook_secret_encrypted = fresh.webhook_secret_encrypted
        return connection.webhook_secret

    @staticmethod
    def _workflow_filter():
        return {"type": "FromWorkflow", "status": list(WORKFLOW_STATUSES)}

    def ensure_feed(self, connection):
        """Configura el feed si no existe o filtra otra cosa. Devuelve si lo
        cambió. A CONFIRMAR con una cuenta real: que la respuesta del GET
        repita el ``filter`` con el que se configuró."""
        try:
            current = _json(self.api_request(connection, "GET", "api/orders/feed/config", permission="Feed v3 and Hook Admin"))
        except ProviderNotFoundError:
            current = {}
        current_filter = (current or {}).get("filter") if isinstance(current, dict) else None
        wanted = self._workflow_filter()
        if isinstance(current_filter, dict) and current_filter.get("type") == wanted["type"] and sorted(
            current_filter.get("status") or []
        ) == sorted(wanted["status"]):
            return False
        self.api_request(
            connection,
            "POST",
            "api/orders/feed/config",
            json_body={"filter": wanted, "queue": FEED_QUEUE},
            permission="Feed v3 and Hook Admin",
        )
        return True

    def register_webhooks(self, connection, url, events):
        """Configura el feed (el respaldo) y el hook (el aviso al instante).
        Idempotente: corre al conectar y en cada repaso, porque VTEX borra un
        hook que pasó 3 días sin avisos. El hook nunca hace fallar el repaso:
        sin él, los pedidos entran igual por el feed. Uno que ya apunta a
        otro sistema no se pisa (cada clave tiene un solo hook)."""
        changed = ["feed"] if self.ensure_feed(connection) else []

        secret = self.ensure_webhook_secret(connection)
        delivery_url = f"{url}?{urlencode({'store': connection.pk})}"
        try:
            try:
                current = _json(self.api_request(connection, "GET", "api/orders/hook/config"))
            except ProviderNotFoundError:
                current = {}
            hook = (current or {}).get("hook") if isinstance(current, dict) else None
            hook = hook if isinstance(hook, dict) else {}
            current_url = _text(hook.get("url"))
            if current_url and not current_url.startswith(url):
                _update_preferences(connection, vtex_hook="foreign")
                _set_last_error(connection, FOREIGN_HOOK_ERROR.format(minutes=self.reconcile_minutes))
                return changed
            headers = hook.get("headers") if isinstance(hook.get("headers"), dict) else {}
            same_secret = hmac.compare_digest(_text(headers.get(HOOK_SECRET_HEADER)), secret)
            if current_url != delivery_url or not same_secret:
                self.api_request(
                    connection,
                    "POST",
                    "api/orders/hook/config",
                    json_body={
                        "filter": self._workflow_filter(),
                        "hook": {"url": delivery_url, "headers": {HOOK_SECRET_HEADER: secret}},
                    },
                    permission="Feed v3 and Hook Admin",
                )
                changed.append("hook")
        except ProviderError as exc:
            # Incluye el 400 de VTEX cuando nuestro endpoint no contestó el
            # ping (sin URL pública, por ejemplo).
            logger.warning("No se pudo configurar el hook de VTEX de la tienda %s: %s", connection.pk, exc)
            _update_preferences(connection, vtex_hook="error")
            return changed

        _update_preferences(connection, vtex_hook="ok")
        if connection.last_error.startswith(FOREIGN_HOOK_ERROR.split(":")[0]):
            _set_last_error(connection, "")
        return changed

    def verify_webhook(self, raw_body, headers, connection=None):
        secret = connection.webhook_secret if connection is not None else ""
        received = get_header(headers, HOOK_SECRET_HEADER).strip()
        if not secret or not received:
            return False
        return hmac.compare_digest(received.encode("utf-8"), secret.encode("utf-8"))

    def parse_webhook(self, raw_body, headers):
        try:
            payload = json.loads((raw_body or b"").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("El aviso debe ser JSON válido.") from None
        if not isinstance(payload, dict):
            raise ValueError("El aviso debe ser un objeto.")
        if _text(payload.get("hookConfig")).lower() == "ping":
            # La prueba que VTEX manda al configurar el hook: solo quiere un 200.
            return None
        order_id = _text(payload.get("OrderId"))
        if not order_id:
            raise ValueError("El aviso no trae 'OrderId'.")
        origin = payload.get("Origin") if isinstance(payload.get("Origin"), dict) else {}
        return WebhookMessage(
            store_id=_text(origin.get("Account")).lower(),
            event_type=ORDER_HOOK_EVENT,
            resource_id=order_id,
            # El worker vuelve a pedir el pedido: en la cola solo queda el id.
            payload={"id": order_id, "state": _text(payload.get("State")), "last_change": _text(payload.get("LastChange"))},
        )
