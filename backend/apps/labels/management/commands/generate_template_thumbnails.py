"""``python manage.py generate_template_thumbnails`` — miniaturas de las
plantillas que no tienen (las creadas antes de que existieran, o una cuyo
diseño no se pudo dibujar). ``--all`` las rehace todas, por ejemplo después de
cambiar el renderer. Idempotente: correrlo dos veces no hace nada nuevo.
"""

from django.core.management.base import BaseCommand

from apps.labels.models import LabelTemplate
from apps.labels.thumbnails import refresh_template_thumbnail


class Command(BaseCommand):
    help = "Genera la miniatura PNG de las plantillas activas que no la tienen (--all: todas)."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Rehacer también las que ya tienen miniatura.")

    def handle(self, *args, all=False, **options):
        templates = LabelTemplate.objects.filter(is_active=True).order_by("pk")
        if not all:
            templates = templates.filter(preview__in=["", None])
        done = failed = 0
        for template in templates:
            if refresh_template_thumbnail(template):
                done += 1
            else:
                failed += 1
                self.stderr.write(f"Sin miniatura: plantilla {template.pk} ({template.name}); ver el log.")
        self.stdout.write(self.style.SUCCESS(f"Miniaturas generadas: {done}. Fallidas: {failed}."))
