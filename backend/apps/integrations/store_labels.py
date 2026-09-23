"""Rótulos pedidos desde el admin de la propia tienda (Labels API).

El otro sentido del flujo que ya existía. Hasta acá el comerciante entraba
a NUESTRA app, elegía pedidos e imprimía (``apps.labels.batch_views``).
Con esto tilda los pedidos en el admin de SU tienda, aprieta "imprimir
etiquetas" y el rótulo aparece ahí: la plataforma nos llama, generamos y
le avisamos dónde bajarlo. El comerciante nunca ve nuestra app.

El ida y vuelta es asincrónico porque la plataforma no espera: el callback
de creación tiene 5 segundos para contestar y el rótulo tiene que estar
resuelto antes de ``STORE_LABEL_TIMEOUT_SECONDS`` (la plataforma lo da por
fallido sola pasados 30 minutos). Por eso acá no se dibuja nada: se guarda
un ``StoreLabelRequest``, se encola ``internal/generate_label`` y el worker
hace el trabajo (ver ``handlers.generate_store_label``).

Dos cosas que no tiene el flujo de nuestra app:

- **La tienda sale de la URL.** Cada tienda registra su propio
  ``callback_labels_url`` con un token firmado que lleva adentro el id de
  la conexión (``make_callback_token``); el payload del callback no dice de
  qué tienda es. Ese token es además lo único que autentica el callback: la
  documentación de Tiendanube no define una firma para estos endpoints,
  a diferencia de los webhooks normales.
- **La descarga es pública.** La plataforma se baja el PDF sin sesión, así
  que cada rótulo tiene su propio token de un solo uso y de vida corta
  (``release_download`` lo invalida apenas la plataforma confirma que ya
  tiene el archivo).
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.core import signing
from django.urls import reverse
from django.utils import timezone

from apps.labels.label_rendering import (
    build_computed_context,
    build_shipment_context,
    render_label_pdf,
)
from apps.labels.models import LabelTemplate

from .events import enqueue_event
from .models import StoreConnection, StoreLabelRequest
from .providers import get_provider
from .providers.base import ProviderError
from .stores import GENERATE_LABEL_EVENT

logger = logging.getLogger(__name__)

CALLBACK_SALT = "apps.integrations.store-label-callback"

# Estados de la etiqueta en la plataforma (Labels API de Tiendanube).
PLATFORM_READY_TO_DOWNLOAD = "READY_TO_DOWNLOAD"
PLATFORM_FAILED = "FAILED"
# Tipo de error genérico: la plataforma tiene un catálogo (balance, límites,
# permisos) que no aplica a un rótulo que generamos nosotros.
PLATFORM_REASON_TYPE = "OTHER_ERROR"

# Feature del plan de la tienda que habilita la Labels API. Sin esto, todo
# endpoint de rótulos responde 403 por más que la app esté bien instalada.
LABEL_API_FEATURE = "fulfillment_order_label_api"
# Claves de StoreConnection.preferences (el modelo ya tiene ese JSON
# justamente para lo que se va definiendo con cada plataforma).
FEATURES_PREFERENCE = "features"
CARRIER_ID_PREFERENCE = "shipping_carrier_id"


def _setting(name, default):
    return getattr(settings, name, default)


# ---------------------------------------------------------------------------
# URLs: la de la plataforma hacia nosotros (callback) y la del PDF
# ---------------------------------------------------------------------------


def make_callback_token(connection):
    """Token firmado que identifica la tienda dentro de la URL del callback.
    Sin vencimiento a propósito: vive lo que viva el alta del carrier en la
    plataforma."""
    return signing.dumps({"c": connection.pk, "p": connection.platform}, salt=CALLBACK_SALT)


def read_callback_token(token, platform):
    """Conexión del token, o ``None`` si está alterado, no corresponde a esta
    plataforma o la tienda ya no existe. Nunca lanza: el que llama contesta
    404 sin contar por qué."""
    try:
        data = signing.loads(token, salt=CALLBACK_SALT)
    except signing.BadSignature:
        return None
    if not isinstance(data, dict) or data.get("p") != platform:
        return None
    return StoreConnection.objects.filter(pk=data.get("c"), platform=platform).first()


def _public_base_url():
    return str(_setting("INTEGRATIONS_PUBLIC_BASE_URL", "") or "").rstrip("/")


def callback_base_url(connection):
    """La URL que se registra como ``callback_labels_url`` de esta tienda.
    La plataforma le agrega sola el sufijo de cada operación
    (``/generate``, ``/cancel``). Vacía si falta
    ``INTEGRATIONS_PUBLIC_BASE_URL``."""
    base = _public_base_url()
    if not base:
        return ""
    path = reverse(
        "tiendanube-label-generate", kwargs={"token": make_callback_token(connection)}
    )
    return f"{base}{path}".removesuffix("/generate")


def download_url(label_request):
    """URL pública del PDF de este rótulo, la que se le pasa a la plataforma
    como ``download_url_from_app``. Vacía si no hay token o falta la URL
    pública del backend."""
    base = _public_base_url()
    if not base or not label_request.download_token:
        return ""
    return base + reverse(
        "tiendanube-label-download", kwargs={"token": label_request.download_token}
    )


# ---------------------------------------------------------------------------
# Entrada: el callback de creación
# ---------------------------------------------------------------------------


def enqueue_generation(label_request):
    """Encola el dibujo de un rótulo. El evento lleva solo el id de la
    fila: el payload del envío ya está guardado y no tiene por qué
    duplicarse (ni pasearse) en la cola."""
    return enqueue_event(
        platform=label_request.connection.platform,
        event_type=GENERATE_LABEL_EVENT,
        connection=label_request.connection,
        resource_id=label_request.external_label_id,
        payload={"label_request_id": label_request.pk},
    )


def intake(connection, items):
    """Guarda un ``StoreLabelRequest`` por cada etiqueta pedida y encola su
    generación. Devuelve ``[(external_label_id, error o "")]`` en el mismo
    orden, para que la vista arme la respuesta por etiqueta.

    Idempotente: un callback repetido (la plataforma reintenta) encuentra la
    fila que ya existe y no vuelve a encolar nada que ya esté resuelto.
    """
    provider = get_provider(connection.platform)
    results = []
    for raw in items:
        try:
            shipment = provider.normalize_label_request(raw)
        except ValueError as exc:
            results.append(("", str(exc)))
            continue

        label_request, created = StoreLabelRequest.objects.get_or_create(
            connection=connection,
            external_label_id=shipment.external_label_id,
            defaults={
                "external_fulfillment_order_id": shipment.external_fulfillment_order_id,
                "payload": raw if isinstance(raw, dict) else {},
            },
        )
        if not created and label_request.status != StoreLabelRequest.Status.PENDING:
            # Ya resuelta: aceptar de nuevo la mandaría a un estado que la
            # plataforma no admite (los finales no vuelven atrás).
            results.append((shipment.external_label_id, ""))
            continue

        enqueue_generation(label_request)
        results.append((shipment.external_label_id, ""))
    return results


# ---------------------------------------------------------------------------
# Generación (la corre el worker)
# ---------------------------------------------------------------------------


def resolve_template(connection):
    """Plantilla con la que se dibuja este rótulo: la que la tienda eligió
    en ``tiendas.html`` si sigue siendo usable, o la pública por defecto (la
    más antigua activa). ``None`` si no hay ninguna."""
    template = connection.default_template
    if template is not None and template.is_active:
        if template.is_public or template.owner_id == connection.owner_id:
            return template
    return LabelTemplate.objects.filter(is_public=True, is_active=True).order_by("id").first()


def _render(connection, shipment):
    template = resolve_template(connection)
    if template is None:
        raise LabelGenerationError(
            "No hay ninguna plantilla pública disponible para dibujar el rótulo."
        )
    context = build_shipment_context(shipment, store=connection)
    computed = build_computed_context(owner=connection.owner, bulto=1, bultos=1)
    pdf_bytes = render_label_pdf(
        template.design,
        template.width_cm,
        template.height_cm,
        context=context,
        logo_file=(connection.logo or None),
        computed=computed,
    )
    return pdf_bytes


class LabelGenerationError(Exception):
    """El rótulo no se puede generar y reintentar no lo va a arreglar
    (plantilla inexistente, diseño roto, payload sin datos del envío)."""


def generate(label_request):
    """Dibuja el PDF, lo guarda y le avisa a la plataforma dónde bajarlo.

    Idempotente en los dos tramos: un reintento después de que el PDF ya se
    generó no lo vuelve a dibujar, solo repite el aviso; una etiqueta ya
    resuelta no hace nada.

    Un error de dibujo se informa como ``FAILED`` a la plataforma y se
    levanta ``LabelGenerationError`` (no tiene sentido reintentarlo). Un
    error hablando con la plataforma se propaga tal cual para que la cola lo
    reintente con backoff.
    """
    if label_request.status in (StoreLabelRequest.Status.READY, StoreLabelRequest.Status.CANCELED):
        return label_request

    connection = label_request.connection
    provider = get_provider(connection.platform)

    if not label_request.file:
        try:
            shipment = provider.normalize_label_request(label_request.payload)
            pdf_bytes = _render(connection, shipment)
        except (ValueError, LabelGenerationError) as exc:
            fail(label_request, str(exc))
            raise LabelGenerationError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - un diseño roto no se arregla reintentando
            logger.exception("Falló el dibujo del rótulo %s", label_request.pk)
            fail(label_request, f"No se pudo dibujar el rótulo: {exc}")
            raise LabelGenerationError(str(exc)) from exc

        label_request.download_token = secrets.token_urlsafe(32)
        label_request.file.save(
            f"rotulo-{label_request.external_label_id}.pdf",
            ContentFile(pdf_bytes),
            save=False,
        )
        label_request.save(update_fields=["file", "download_token", "updated_at"])

    url = download_url(label_request)
    if not url:
        # Sin URL pública la plataforma no puede bajar nada: es un problema
        # de configuración del servidor, no de este rótulo.
        message = (
            "No se puede publicar el rótulo: falta INTEGRATIONS_PUBLIC_BASE_URL "
            "(la URL pública HTTPS del backend)."
        )
        fail(label_request, message)
        raise LabelGenerationError(message)

    provider.push_label_status(
        connection,
        label_request.external_fulfillment_order_id,
        label_request.external_label_id,
        status=PLATFORM_READY_TO_DOWNLOAD,
        documents=[
            {
                "file_name": label_request.file.name.rsplit("/", 1)[-1],
                "type": "LABEL",
                "format": "PDF",
                "download_url_from_app": url,
                "size": label_request.file.size,
            }
        ],
    )
    label_request.status = StoreLabelRequest.Status.READY
    label_request.error_message = ""
    label_request.save(update_fields=["status", "error_message", "updated_at"])
    return label_request


def fail(label_request, message):
    """Marca el rótulo como fallido y se lo informa a la plataforma.

    El aviso se hace con el mayor esfuerzo posible: si la plataforma no
    contesta, igual queda el estado local con el motivo. Lo que NO puede
    pasar es quedarse callados — la etiqueta le queda colgada al comerciante
    hasta que la plataforma la mata sola media hora después.
    """
    label_request.status = StoreLabelRequest.Status.FAILED
    label_request.error_message = (message or "")[:2000]
    # Un rótulo fallido no se sirve: si alcanzó a generarse un PDF, deja de
    # estar publicado en el momento en que se da por perdido.
    label_request.download_token = ""
    label_request.save(
        update_fields=["status", "error_message", "download_token", "updated_at"]
    )

    connection = label_request.connection
    try:
        get_provider(connection.platform).push_label_status(
            connection,
            label_request.external_fulfillment_order_id,
            label_request.external_label_id,
            status=PLATFORM_FAILED,
            reason={"type": PLATFORM_REASON_TYPE, "message": label_request.error_message[:300]},
        )
    except ProviderError as exc:
        logger.warning(
            "No se pudo informar el fallo del rótulo %s a la tienda %s: %s",
            label_request.pk,
            connection.pk,
            exc,
        )
    return label_request


# ---------------------------------------------------------------------------
# Cancelación, liberación y vencimiento
# ---------------------------------------------------------------------------


def _apply_platform_status(connection, pairs, status, *, publish):
    """Aplica de nuestro lado lo que la plataforma decidió sobre un rótulo.
    ``pairs`` son ``(fulfillment_order_id, label_id)``; ``publish`` dice si
    después de esto el PDF se sigue sirviendo.

    Devuelve ``[(label_id, error o "")]``. Un rótulo que no tenemos se
    acepta igual: no hay nada que hacer de nuestro lado, y rechazarlo solo
    le dejaría la etiqueta trabada al comerciante.
    """
    results = []
    for _fulfillment_order_id, label_id in pairs:
        label_request = StoreLabelRequest.objects.filter(
            connection=connection, external_label_id=label_id
        ).first()
        if label_request is not None:
            label_request.status = status
            if not publish:
                label_request.download_token = ""
                label_request.released_at = timezone.now()
            label_request.save(
                update_fields=["status", "download_token", "released_at", "updated_at"]
            )
        results.append((label_id, ""))
    return results


def cancel(connection, pairs):
    """La plataforma cancela un rótulo. De nuestro lado es dejar de servir
    el PDF: no hay nada que darle de baja en un courier.

    No se le informa nada a la plataforma: es ella la que pregunta, y el
    estado final lo escribe según lo que contestemos.
    """
    return _apply_platform_status(
        connection, pairs, StoreLabelRequest.Status.CANCELED, publish=False
    )


def suspend(connection, pairs):
    """Suspensión: igual que cancelar, pero puede volver atrás
    (``reactivate``). La plataforma solo suspende etiquetas que ya tiene
    guardadas, así que dejar de publicar el PDF no le saca nada."""
    return _apply_platform_status(
        connection, pairs, StoreLabelRequest.Status.SUSPENDED, publish=False
    )


def reactivate(connection, pairs):
    """Reactivación de una etiqueta suspendida. No se vuelve a publicar el
    PDF: a esta altura la plataforma ya lo tiene guardado (por eso pudo
    suspenderla), y nuestra URL no hace falta más."""
    return _apply_platform_status(
        connection, pairs, StoreLabelRequest.Status.READY, publish=False
    )


def release_download(label_request):
    """Invalida el token: la plataforma ya se bajó el PDF y lo hostea ella.
    A partir de acá la URL pública deja de existir."""
    if not label_request.download_token:
        return label_request
    label_request.download_token = ""
    label_request.released_at = timezone.now()
    label_request.save(update_fields=["download_token", "released_at", "updated_at"])
    return label_request


def expire_stale_requests():
    """Marca como fallidos los rótulos que quedaron pendientes demasiado
    tiempo (el worker se cayó, la cola agotó los reintentos) y se lo informa
    a la plataforma.

    Corre con margen sobre los 30 minutos que espera la plataforma: más vale
    un "falló" nuestro, con motivo, que el vencimiento silencioso de ella.
    Lo llama el worker en cada vuelta (``run_integrations_worker``).
    """
    timeout = _setting("STORE_LABEL_TIMEOUT_SECONDS", 1200)
    deadline = timezone.now() - timedelta(seconds=timeout)
    stale = StoreLabelRequest.objects.filter(
        status=StoreLabelRequest.Status.PENDING, created_at__lt=deadline
    ).select_related("connection")
    count = 0
    for label_request in stale:
        fail(
            label_request,
            f"El rótulo no se generó dentro de los {timeout // 60} minutos de plazo.",
        )
        count += 1
    return count


# ---------------------------------------------------------------------------
# Alta del medio de envío en la tienda
# ---------------------------------------------------------------------------


def supports_label_api(connection):
    """Si el plan de esta tienda incluye la Labels API. ``None`` = todavía
    no se sabe (tienda conectada antes de que se guardaran las features).

    Sirve para avisarle al comerciante que su plan no alcanza ANTES de que
    apriete "imprimir etiquetas", en vez de que se entere con un 403.
    """
    preferences = connection.preferences or {}
    if FEATURES_PREFERENCE not in preferences:
        return None
    features = preferences.get(FEATURES_PREFERENCE) or []
    return LABEL_API_FEATURE in [str(feature) for feature in features]


def carrier_name():
    return str(_setting("STORE_LABEL_CARRIER_NAME", "") or "Rótulos")


def register_carrier(connection):
    """Da de alta nuestro medio de envío en la tienda, con el callback de
    rótulos ya apuntado a esta conexión.

    NO corre solo al conectar una tienda, y es a propósito: desde que el
    carrier existe, esa tienda nos pregunta el precio del envío en cada
    checkout y nos muestra como opción de envío a sus compradores. Eso se
    enciende cliente por cliente y a conciencia, no como efecto secundario
    de instalar la app.

    Exige que la tienda tenga tarifas cargadas: darla de alta sin tabla la
    dejaría ofreciendo un medio de envío que nunca cotiza (ver
    ``shipping_rates.quote``). Devuelve el carrier de la plataforma.
    """
    labels_url = callback_base_url(connection)
    if not labels_url:
        raise LabelGenerationError(
            "No se puede dar de alta el medio de envío: falta INTEGRATIONS_PUBLIC_BASE_URL "
            "(la URL pública HTTPS del backend)."
        )
    from .shipping_rates import rates_callback_url

    rates_url = rates_callback_url(connection)
    if not rates_url:
        raise LabelGenerationError(
            "No se puede dar de alta el medio de envío: falta INTEGRATIONS_PUBLIC_BASE_URL."
        )
    if not connection.shipping_rates.filter(is_active=True).exists():
        raise LabelGenerationError(
            "Esta tienda no tiene tarifas de envío cargadas. Sin tabla no podemos cotizar, y el "
            "medio de envío aparecería en el checkout sin precio. Cargalas antes de darlo de alta."
        )

    carrier = get_provider(connection.platform).register_shipping_carrier(
        connection,
        name=carrier_name(),
        rates_url=rates_url,
        labels_url=labels_url,
    )
    if isinstance(carrier, dict) and carrier.get("id"):
        preferences = dict(connection.preferences or {})
        preferences[CARRIER_ID_PREFERENCE] = str(carrier["id"])
        connection.preferences = preferences
        connection.save(update_fields=["preferences", "updated_at"])
    return carrier
