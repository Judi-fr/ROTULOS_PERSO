"""``python manage.py erd_mermaid`` — el diagrama entidad-relación de la base,
en Mermaid (``erDiagram``), generado desde los modelos.

Se genera y no se dibuja a mano para que no se desactualice: lo que sale es
lo que Django tiene cargado. Solo entran los modelos de ``apps.*``; los de
Django a los que apuntan (``User``, ``Group``) aparecen como entidades sin
columnas, para que la relación se vea sin llenar el diagrama de tablas ajenas.

``--output docs/erd.md`` lo escribe envuelto en un bloque ```mermaid``` (lo
que GitHub y Azure DevOps dibujan); sin ``--output`` imprime el diagrama solo.
"""

from __future__ import annotations

from pathlib import Path

from django.apps import apps
from django.core.management.base import BaseCommand

APP_PREFIX = "apps."


def own_models():
    return [
        model
        for config in apps.get_app_configs()
        if config.name.startswith(APP_PREFIX)
        for model in config.get_models()
    ]


def entity_names(all_models):
    """Nombre de cada modelo en el diagrama: la clase, o ``app_Clase`` si dos
    apps tienen una clase con el mismo nombre."""
    counts = {}
    for model in all_models:
        counts[model.__name__] = counts.get(model.__name__, 0) + 1
    return {
        model: model.__name__ if counts[model.__name__] == 1 else f"{model._meta.app_label}_{model.__name__}"
        for model in all_models
    }


def column_type(field):
    if field.is_relation:
        return field.target_field.get_internal_type().removesuffix("Field").lower() or "id"
    return field.get_internal_type().removesuffix("Field").lower() or "field"


def column_key(field):
    if field.primary_key:
        return " PK"
    if field.is_relation:
        return " FK"
    if field.unique:
        return " UK"
    return ""


def relation_line(field, names):
    source = names[field.model]
    target = names[field.related_model]
    if field.many_to_many:
        connector = "}o--o{"
        return f'    {source} {connector} {target} : "{field.name}"'
    if field.one_to_one:
        connector = "|o--o|" if field.null else "||--o|"
    else:
        connector = "|o--o{" if field.null else "||--o{"
    return f'    {target} {connector} {source} : "{field.name}"'


def build_diagram():
    own = own_models()
    related = []
    for model in own:
        for field in model._meta.get_fields():
            if field.concrete and field.is_relation and field.related_model not in own and field.related_model not in related:
                related.append(field.related_model)
            if field.many_to_many and not field.auto_created and field.related_model not in own and field.related_model not in related:
                related.append(field.related_model)
    names = entity_names(own + related)

    lines = ["erDiagram"]
    for model in sorted(own, key=lambda m: (m._meta.app_label, m.__name__)):
        lines.append(f"    {names[model]} {{")
        for field in model._meta.concrete_fields:
            column = field.attname if field.is_relation else field.name
            lines.append(f"        {column_type(field)} {column}{column_key(field)}")
        lines.append("    }")
    for model in sorted(related, key=lambda m: (m._meta.app_label, m.__name__)):
        lines.append(f"    {names[model]} {{")
        lines.append(f"        {column_type(model._meta.pk)} {model._meta.pk.name} PK")
        lines.append("    }")

    relations = []
    for model in own:
        for field in model._meta.get_fields():
            if field.auto_created or not field.is_relation or field.related_model is None:
                continue
            if field.concrete or field.many_to_many:
                relations.append(relation_line(field, names))
    lines.extend(sorted(relations))
    return "\n".join(lines) + "\n"


class Command(BaseCommand):
    help = "Imprime (o escribe en --output) el diagrama entidad-relación en Mermaid."

    def add_arguments(self, parser):
        parser.add_argument("--output", help="Archivo .md donde escribirlo, p. ej. ../docs/erd.md")

    def handle(self, *args, output=None, **options):
        diagram = build_diagram()
        if not output:
            self.stdout.write(diagram, ending="")
            return
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Diagrama entidad-relación\n\n"
            "Generado con `python manage.py erd_mermaid --output ../docs/erd.md` (desde `backend/`). "
            "No se edita a mano: se vuelve a generar cuando cambian los modelos.\n\n"
            f"```mermaid\n{diagram}```\n",
            encoding="utf-8",
        )
        self.stdout.write(self.style.SUCCESS(f"ERD escrito en {path}"))
