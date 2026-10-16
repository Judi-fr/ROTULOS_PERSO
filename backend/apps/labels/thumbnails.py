"""Miniatura PNG de una ``LabelTemplate`` (el campo ``preview``), la que
``frontend/mis_plantillas.html`` muestra en cada tarjeta.

Sale del MISMO PDF que la vista previa (``LabelTemplateViewSet.preview``:
``render_label_pdf`` con ``SAMPLE_LABEL_CONTEXT``) rasterizado con pypdfium2,
así la miniatura no puede mostrar algo distinto de lo que se imprime. Antes el
campo existía pero nadie lo llenaba, y cada tarjeta decía "Ver vista previa".

Es un accesorio: si el diseño no se puede dibujar, se registra y la plantilla
se guarda igual, sin miniatura.
"""

from __future__ import annotations

import logging
from io import BytesIO

import pypdfium2
from django.core.files.base import ContentFile

from .label_rendering import SAMPLE_LABEL_CONTEXT, build_computed_context, render_label_pdf

logger = logging.getLogger(__name__)

# Ancho de la imagen en píxeles: alcanza para la tarjeta (y para pantallas de
# alta densidad) sin pesar más que unas decenas de KB.
THUMBNAIL_WIDTH_PX = 480
PDF_POINTS_PER_CM = 72 / 2.54


def render_template_thumbnail(template) -> bytes:
    """PNG de la primera (única) página del rótulo de ejemplo de la plantilla."""
    pdf_bytes = render_label_pdf(
        design=template.design,
        width_cm=template.width_cm,
        height_cm=template.height_cm,
        context=SAMPLE_LABEL_CONTEXT,
        # Vista previa: {{secuencia}} no consume el contador real.
        computed=build_computed_context(owner=template.owner, is_preview=True),
    )
    document = pypdfium2.PdfDocument(pdf_bytes)
    try:
        page = document[0]
        scale = THUMBNAIL_WIDTH_PX / page.get_width()
        image = page.render(scale=scale).to_pil()
    finally:
        document.close()
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def refresh_template_thumbnail(template) -> bool:
    """Regenera y guarda ``template.preview``. Nunca lanza: devuelve si pudo."""
    try:
        png = render_template_thumbnail(template)
    except Exception:
        logger.exception("No se pudo generar la miniatura de la plantilla %s", template.pk)
        return False
    if template.preview:
        template.preview.delete(save=False)
    template.preview.save(f"plantilla-{template.pk}.png", ContentFile(png), save=False)
    # Solo el campo de la imagen: no pisar lo que otro request haya guardado.
    type(template).objects.filter(pk=template.pk).update(preview=template.preview.name)
    return True
