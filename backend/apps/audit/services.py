"""Punto único para dejar un rastro de auditoría.

Se llama explícitamente desde las vistas que lo necesitan (nada de señales
globales: menos magia, más fácil de testear y de seguir el flujo). Nunca
rompe el flujo principal: cualquier error al registrar queda solo logueado,
porque una falla acá no puede tumbar un login, un alta de usuario, etc.
"""

from __future__ import annotations

import logging

from django.db import transaction

from .models import AuditLog

logger = logging.getLogger(__name__)


def _extract_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        # El primer valor de la cadena es el cliente original.
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None


def _extract_user_agent(request):
    if request is None:
        return ""
    return (request.META.get("HTTP_USER_AGENT") or "")[:300]


def record(
    request=None,
    *,
    actor=None,
    category,
    action,
    target=None,
    target_type="",
    target_id="",
    target_repr="",
    changes=None,
    actor_email="",
):
    """Crea un ``AuditLog``.

    - ``actor``: si no se indica, se toma de ``request.user`` (si está
      autenticado).
    - ``actor_email``: si no se indica, se toma de ``actor.email``. Para
      eventos sin actor autenticado (login fallido, por ejemplo) hay que
      pasarlo explícitamente con el email intentado.
    - ``target``: instancia relacionada con el evento (un ``User``, un
      ``Group``, un ``Order``, ...). Si se pasa, completa ``target_type``/
      ``target_id``/``target_repr`` con valores razonables salvo que ya
      vengan indicados explícitamente.
    """

    try:
        if actor is None and request is not None:
            user = getattr(request, "user", None)
            if user is not None and getattr(user, "is_authenticated", False):
                actor = user

        if not actor_email and actor is not None:
            actor_email = getattr(actor, "email", "") or ""

        if target is not None:
            target_type = target_type or target.__class__.__name__.lower()
            target_id = target_id or str(getattr(target, "pk", "") or "")
            target_repr = target_repr or str(target)

        # ``atomic()`` (savepoint si ya hay una transacción abierta, p. ej. la
        # que envuelve cada test) evita que un fallo acá deje inutilizable la
        # transacción del flujo principal que la llama.
        with transaction.atomic():
            AuditLog.objects.create(
                actor=actor,
                actor_email=actor_email,
                category=category,
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id else "",
                target_repr=target_repr,
                changes=changes or {},
                ip_address=_extract_ip(request),
                user_agent=_extract_user_agent(request),
            )
    except Exception:  # noqa: BLE001 - una auditoría fallida no puede tumbar el flujo principal
        logger.exception(
            "No se pudo registrar el evento de auditoría (%s/%s)", category, action
        )
