"""Conexión de tiendas online: instalación OAuth, vínculo con una cuenta y
desconexión.

Un comerciante llega a la instalación por dos caminos:

1. Desde nuestra web ("Conectar Tiendanube"): ya está logueado, así que la
   URL de autorización lleva un ``state`` firmado con su usuario
   (``make_oauth_state``) y al volver la tienda queda vinculada a él.
2. Desde la tienda de apps de la plataforma: vuelve SIN ``state``, no
   sabemos quién es. La tienda se guarda sin ``owner`` y se lo manda a
   nuestra web con un token de reclamo firmado y de vida corta
   (``make_claim_token``); al iniciar sesión, ``claim_store`` la vincula.

Identidad siempre desde el usuario autenticado o un dato firmado por el
backend, nunca desde un id que mande el cliente.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.utils import timezone

from .events import enqueue_event
from .models import StoreConnection
from .providers.base import ProviderError

STATE_SALT = "apps.integrations.store-oauth-state"
CLAIM_SALT = "apps.integrations.store-claim"

# Eventos INTERNOS de la cola (no los manda la plataforma: el receptor de
# webhooks descarta cualquier aviso con este prefijo). Sus handlers están en
# apps.integrations.handlers.
INTERNAL_EVENT_PREFIX = "internal/"
STORE_SETUP_EVENT = "internal/store_setup"
IMPORT_ORDERS_EVENT = "internal/import_orders"
PUSH_FULFILLMENT_EVENT = "internal/push_fulfillment"
GENERATE_LABEL_EVENT = "internal/generate_label"


def enqueue_store_setup(connection):
    """Registrar webhooks (e importar, si ya tiene dueño) tras conectar."""
    return enqueue_event(
        platform=connection.platform,
        event_type=STORE_SETUP_EVENT,
        connection=connection,
        payload={"connected_at": connection.connected_at.isoformat()},
    )


def enqueue_initial_import(connection):
    """Importación de los pedidos de los últimos
    ``INTEGRATIONS_INITIAL_IMPORT_DAYS`` días, empezando por la página 1
    (cada página encola la siguiente, ver handlers.import_orders_page)."""
    days = getattr(settings, "INTEGRATIONS_INITIAL_IMPORT_DAYS", 30)
    since = (timezone.now() - timedelta(days=days)).replace(microsecond=0).isoformat()
    return enqueue_event(
        platform=connection.platform,
        event_type=IMPORT_ORDERS_EVENT,
        connection=connection,
        resource_id="1",
        payload={"page": 1, "created_at_min": since},
    )


class StoreClaimError(Exception):
    def __init__(self, message, *, conflict=False):
        super().__init__(message)
        self.conflict = conflict


@dataclass
class ConnectResult:
    connection: StoreConnection
    created: bool
    # Instalada sin usuario identificado: hay que reclamarla.
    needs_claim: bool
    # Ya estaba vinculada a OTRA cuenta que la del usuario del state.
    owner_conflict: bool


def make_oauth_state(user, platform):
    return signing.dumps({"u": user.pk, "p": platform}, salt=STATE_SALT)


def read_oauth_state(state, platform):
    """Usuario del ``state`` del callback, o ``None`` si no vino (instalación
    desde la tienda de apps). ``signing.BadSignature`` si fue alterado, venció
    o no corresponde: el callback lo trata como un intento inválido."""
    if not state:
        return None
    max_age = getattr(settings, "INTEGRATIONS_OAUTH_STATE_MAX_AGE_SECONDS", 900)
    data = signing.loads(state, salt=STATE_SALT, max_age=max_age)
    if not isinstance(data, dict) or data.get("p") != platform:
        raise signing.BadSignature("El state no corresponde a esta plataforma.")
    user = get_user_model().objects.filter(pk=data.get("u"), is_active=True).first()
    if user is None:
        raise signing.BadSignature("El usuario del state no existe o está inactivo.")
    return user


def make_claim_token(connection):
    return signing.dumps(
        {"c": connection.pk, "s": connection.external_store_id, "p": connection.platform},
        salt=CLAIM_SALT,
    )


def connect_store(provider, code, user=None):
    """Canjea ``code`` y crea o actualiza la ``StoreConnection``.

    Reinstalar una tienda ya conocida actualiza su token (nunca crea otra
    fila). Si ya pertenece a otra cuenta, el token se actualiza igual —quien
    instaló administra esa tienda en la plataforma— pero la cuenta dueña no
    se reasigna. Los errores del canje (``ProviderError``) se propagan; no
    poder leer el nombre de la tienda después no impide conectarla.
    """
    oauth = provider.exchange_code(code)
    owner_conflict = False

    with transaction.atomic():
        connection = (
            StoreConnection.objects.select_for_update()
            .filter(platform=provider.platform, external_store_id=oauth.external_store_id)
            .first()
        )
        created = connection is None
        if created:
            connection = StoreConnection(
                platform=provider.platform,
                external_store_id=oauth.external_store_id,
                owner=user,
            )
        elif user is not None:
            if connection.owner_id is None:
                connection.owner = user
            elif connection.owner_id != user.pk:
                owner_conflict = True

        connection.access_token = oauth.access_token
        connection.scopes = oauth.scopes[:500]
        connection.status = StoreConnection.Status.ACTIVE
        connection.last_error = ""
        connection.connected_at = timezone.now()
        connection.disconnected_at = None
        connection.save()

    try:
        info = provider.get_store_info(connection)
    except ProviderError as exc:
        connection.last_error = f"No se pudieron leer los datos de la tienda: {exc}"[:2000]
        connection.save(update_fields=["last_error", "updated_at"])
    else:
        connection.name = info.name[:150] or connection.name
        connection.store_url = info.store_url[:200] or connection.store_url
        # Qué incluye el plan de esa tienda. Se guarda tal cual lo devuelve
        # la plataforma: quién lo consulta decide qué feature le importa
        # (ver store_labels.supports_label_api).
        connection.preferences = dict(connection.preferences or {}, features=list(info.features))
        connection.save(update_fields=["name", "store_url", "preferences", "updated_at"])

    # Registrar webhooks e importar pedidos lleva varias llamadas a la API:
    # lo hace el worker, el callback solo vuelve rápido al frontend.
    enqueue_store_setup(connection)

    return ConnectResult(
        connection=connection,
        created=created,
        needs_claim=connection.owner_id is None,
        owner_conflict=owner_conflict,
    )


def claim_store(token, user):
    """Vincula a ``user`` la tienda del token de reclamo. Devuelve
    ``(connection, claimed)``; ``claimed=False`` si ya era suya (reintento).
    ``StoreClaimError`` si el token no sirve o la tienda es de otra cuenta."""
    max_age = getattr(settings, "INTEGRATIONS_STORE_CLAIM_MAX_AGE_SECONDS", 1800)
    try:
        data = signing.loads(token, salt=CLAIM_SALT, max_age=max_age)
    except signing.SignatureExpired:
        raise StoreClaimError(
            "El enlace para vincular la tienda venció. Volvé a abrir la app desde el panel de tu tienda."
        ) from None
    except signing.BadSignature:
        raise StoreClaimError("El enlace para vincular la tienda no es válido.") from None
    if not isinstance(data, dict):
        raise StoreClaimError("El enlace para vincular la tienda no es válido.")

    with transaction.atomic():
        connection = (
            StoreConnection.objects.select_for_update()
            .filter(pk=data.get("c"), external_store_id=data.get("s"), platform=data.get("p"))
            .first()
        )
        if connection is None:
            raise StoreClaimError("El enlace para vincular la tienda no es válido.")
        if connection.owner_id is None:
            connection.owner = user
            connection.save(update_fields=["owner", "updated_at"])
            # Recién ahora hay a nombre de quién crear sus pedidos.
            enqueue_initial_import(connection)
            return connection, True
        if connection.owner_id == user.pk:
            return connection, False
    raise StoreClaimError("Esta tienda ya está vinculada a otra cuenta.", conflict=True)


def disconnect_store(connection):
    """Desconecta sin borrar: descarta el token y deja de procesar la tienda.
    Desinstalar la app en sí se hace desde el panel de la plataforma."""
    connection.status = StoreConnection.Status.REVOKED
    connection.access_token = ""
    connection.disconnected_at = timezone.now()
    connection.save(update_fields=["status", "access_token_encrypted", "disconnected_at", "updated_at"])
    return connection
