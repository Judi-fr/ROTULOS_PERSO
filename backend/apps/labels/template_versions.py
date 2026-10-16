"""Historial de versiones de las plantillas (``LabelTemplateVersion``).

Una versión es lo que se dibuja: nombre, tamaño y diseño. Se crea la 1 al
crear la plantilla y una nueva cuando un guardado cambia algo de eso; un
guardado que no cambia el dibujo (marcarla pública, la descripción) no deja
una versión vacía. Restaurar copia una versión vieja sobre la plantilla y lo
registra como versión NUEVA: el historial nunca se reescribe.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Max

from .models import LabelTemplate, LabelTemplateVersion

# Lo que se copia entre la plantilla y una versión.
VERSIONED_FIELDS = ("name", "width_cm", "height_cm", "design")
NOTE_MAX_LENGTH = LabelTemplateVersion._meta.get_field("note").max_length


def _snapshot(source):
    return {
        "name": source.name,
        "width_cm": Decimal(source.width_cm),
        "height_cm": Decimal(source.height_cm),
        "design": source.design or {},
    }


def latest_version(template):
    return template.versions.order_by("-number").first()


def record_version(template, *, user=None, note="", restored_from=None, force=False):
    """Guarda el estado actual de ``template`` como su próxima versión.

    Sin ``force``, no hace nada si es igual a la última (devuelve ``None``).
    El número sale con la plantilla bloqueada: dos guardados a la vez no
    pueden tomar el mismo.
    """
    with transaction.atomic():
        LabelTemplate.objects.select_for_update().filter(pk=template.pk).first()
        last = latest_version(template)
        snapshot = _snapshot(template)
        if last is not None and not force and _snapshot(last) == snapshot:
            return None
        number = (template.versions.aggregate(top=Max("number"))["top"] or 0) + 1
        return LabelTemplateVersion.objects.create(
            template=template,
            number=number,
            note=(note or "").strip()[:NOTE_MAX_LENGTH],
            restored_from=restored_from,
            created_by=user if getattr(user, "is_authenticated", False) else None,
            **snapshot,
        )


def restore_version(template, version, *, user=None):
    """Vuelve la plantilla a ``version`` y lo deja como versión nueva."""
    for field, value in _snapshot(version).items():
        setattr(template, field, value)
    template.save(update_fields=[*VERSIONED_FIELDS, "updated_at"])
    return record_version(
        template, user=user, note=f"Restaurada la versión {version.number}", restored_from=version.number, force=True
    )
