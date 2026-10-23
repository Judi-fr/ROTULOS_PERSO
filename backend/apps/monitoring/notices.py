"""Avisos al comerciante: lo que se rompió en SU comercio y no se arregla
solo. El monitoreo de ``alerts.py`` le avisa al admin de la plataforma; esto
le avisa al dueño del comercio.

``sync_all`` (lo corre ``run_monitor`` cada minuto, y el worker si el monitor
no late) mira el estado actual y deja un ``MerchantNotice`` abierto por cada
problema: tienda con error, cuenta de transportista rechazada, y en los
últimos ``NOTICES_RECENT_DAYS`` un lote, un trabajo de impresión o un rótulo
pedido por la tienda que fallaron. Lo que se arregló se cierra. Lo nuevo va en
UN mail por comercio y por pasada al dueño; si el mail falla se reintenta,
pero no antes de ``NOTICES_MAIL_RETRY_SECONDS``.

En el panel cada miembro ve los avisos de las áreas a las que tiene acceso, y
de una tienda solo si la tiene habilitada (``visible_notices``).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import MerchantNotice

logger = logging.getLogger(__name__)

# Lo que no es de una tienda conocida (un lote anterior a los equipos): un
# empleado limitado a algunas tiendas no lo ve.
UNKNOWN_STORE = "unknown"


def _recent(now):
    return now - timedelta(days=settings.NOTICES_RECENT_DAYS)


def detect(now):
    """``{(owner_id, key): dict}`` con todos los problemas de ahora."""
    from apps.carriers.models import CarrierAccount
    from apps.carriers.providers import get_provider
    from apps.documents.models import Document
    from apps.integrations.models import StoreConnection, StoreLabelRequest
    from apps.printing.models import PrintJob

    found = {}

    def add(owner_id, key, **notice):
        found[(owner_id, key)] = notice

    for store in StoreConnection.objects.filter(status=StoreConnection.Status.ERROR).exclude(owner=None):
        name = store.name or store.external_store_id
        add(
            store.owner_id, f"store:{store.pk}:error", kind="store_error", access="stores", store_ids=[store.pk],
            title=f"La tienda {name} tiene un error de conexión", detail=store.last_error, url="tiendas.html",
        )
    for account in CarrierAccount.objects.filter(is_active=False).exclude(last_error=""):
        provider = get_provider(account.carrier)
        carrier = provider.name if provider else account.carrier
        add(
            account.owner_id, f"carrier:{account.pk}", kind="carrier_auth", access="carriers", store_ids=None,
            title=f"Tu cuenta de {carrier} quedó desactivada", detail=account.last_error,
            url=f"transportista.html?carrier={account.carrier}",
        )
    for document in Document.objects.filter(status=Document.Status.FAILED, is_active=True, updated_at__gte=_recent(now)):
        add(
            document.user_id, f"document:{document.pk}", kind="batch_failed", access="labels.print",
            store_ids=document.store_ids if document.store_ids is not None else [UNKNOWN_STORE],
            title=f"No se pudo generar «{document.name}»", detail=document.error_message, url="documentos.html",
        )
    for job in PrintJob.objects.filter(status=PrintJob.Status.ERROR, updated_at__gte=_recent(now)).select_related(
        "printer", "document"
    ):
        stores = job.document.store_ids if job.document and job.document.store_ids is not None else [UNKNOWN_STORE]
        printer = job.printer.name if job.printer else "una impresora"
        add(
            job.owner_id, f"printjob:{job.pk}", kind="print_error", access="labels.print", store_ids=stores,
            title=f"No se pudo imprimir en {printer}", detail=job.error_message, url="cola_impresion.html",
        )
    for label in StoreLabelRequest.objects.filter(
        status=StoreLabelRequest.Status.FAILED, updated_at__gte=_recent(now)
    ).select_related("connection"):
        if not label.connection.owner_id:
            continue
        name = label.connection.name or label.connection.external_store_id
        add(
            label.connection.owner_id, f"storelabel:{label.pk}", kind="store_label_failed", access="orders.view",
            store_ids=[label.connection_id], title=f"La tienda {name} pidió un rótulo que no salió",
            detail=label.error_message, url="rotulos_tienda.html",
        )
    return found


def sync_all(now=None):
    """Abre, actualiza y cierra los avisos; manda los mails. Devuelve cuántos
    avisos nuevos hubo."""
    now = now or timezone.now()
    found = detect(now)
    open_notices = {(n.owner_id, n.key): n for n in MerchantNotice.objects.filter(resolved_at=None)}
    created = 0
    for key, data in found.items():
        notice = open_notices.get(key)
        if notice is None:
            MerchantNotice.objects.create(owner_id=key[0], key=key[1], **data)
            created += 1
        elif any(getattr(notice, field) != value for field, value in data.items()):
            for field, value in data.items():
                setattr(notice, field, value)
            notice.save()
    gone = [notice.pk for key, notice in open_notices.items() if key not in found]
    if gone:
        MerchantNotice.objects.filter(pk__in=gone).update(resolved_at=now)
    _mail_new(now)
    return created


def _mail_new(now):
    from apps.accounts.teams import business_display_name, mail_person

    if not settings.NOTICES_MAIL_ENABLED:
        return
    retry = timedelta(seconds=settings.NOTICES_MAIL_RETRY_SECONDS)
    pending = (
        MerchantNotice.objects.filter(resolved_at=None, dismissed_at=None, mailed_at=None)
        .exclude(mail_attempted_at__gt=now - retry)
        .select_related("owner")
        .order_by("owner_id", "created_at")
    )
    by_owner = {}
    for notice in pending:
        by_owner.setdefault(notice.owner, []).append(notice)
    for owner, items in by_owner.items():
        ids = [notice.pk for notice in items]
        # Los avisos cuelgan del usuario de datos del comercio: el mail va a
        # la persona dueña.
        person = mail_person(owner)
        if person is None or not person.email:
            MerchantNotice.objects.filter(pk__in=ids).update(mail_attempted_at=now)
            continue
        try:
            _send(person, items, business_display_name(owner))
        except Exception:  # noqa: BLE001 - un SMTP caído no frena el monitoreo
            logger.exception("No se pudo mandar el aviso a %s", person.email)
            MerchantNotice.objects.filter(pk__in=ids).update(mail_attempted_at=now)
            continue
        MerchantNotice.objects.filter(pk__in=ids).update(mailed_at=now, mail_attempted_at=now)


def _send(person, items, business):
    subject = items[0].title if len(items) == 1 else f"{len(items)} cosas para revisar en tu comercio"
    # Una persona puede ser dueña de varios comercios: el mail dice cuál.
    lines = [f"Hay algo para revisar en tu comercio «{business}»:", ""]
    for notice in items:
        lines.append(f"- {notice.title}")
        if notice.detail:
            lines.append(f"  {notice.detail[:300]}")
    lines += ["", f"Ver los avisos: {settings.FRONTEND_URL.rstrip('/')}/notificaciones.html"]
    send_mail(f"{settings.MONITORING_SUBJECT_PREFIX} {subject}", "\n".join(lines), settings.DEFAULT_FROM_EMAIL, [person.email])


def visible_notices(user):
    """Los avisos abiertos que ``user`` puede ver en su comercio."""
    from apps.accounts.teams import allowed_store_ids, data_owner, member_accesses

    accesses = member_accesses(user)
    allowed = allowed_store_ids(user)
    rows = MerchantNotice.objects.filter(owner=data_owner(user), resolved_at=None, dismissed_at=None)
    return [
        notice
        for notice in rows
        if notice.access in accesses
        and (notice.store_ids is None or allowed is None or set(notice.store_ids) <= allowed)
    ]
