"""Biblioteca de imágenes del comercio (``LabelImage``): normalizar lo que
se sube y buscar el archivo. La preparación para dibujar (encuadre, recorte)
se agrega acá mismo más adelante."""

import uuid
from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError
from rest_framework import serializers

MAX_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_SIDE_PX = 2000
MAX_IMAGES_PER_OWNER = 300
MAX_PIXELS = 40_000_000  # contra archivos chicos que se abren enormes
TOO_BIG_MESSAGE = "La imagen es demasiado grande (máximo 40 megapíxeles)."


def normalize_upload(uploaded):
    """La imagen subida, con la orientación EXIF aplicada y los datos EXIF
    fuera, con el lado mayor en ``MAX_SIDE_PX`` como mucho. PNG si tiene
    transparencia o viene de PNG/GIF, si no JPEG calidad 90. Nombre al azar: el archivo es
    público (ver el Caddyfile) y no debe poder adivinarse."""
    try:
        uploaded.seek(0)
        image = Image.open(uploaded)
        if image.width * image.height > MAX_PIXELS:
            raise serializers.ValidationError(TOO_BIG_MESSAGE)
        image.seek(0)  # GIF animado: el primer cuadro
        source_format = image.format
        image.load()
    except Image.DecompressionBombError:
        raise serializers.ValidationError(TOO_BIG_MESSAGE)
    except (UnidentifiedImageError, OSError, ValueError, EOFError):
        raise serializers.ValidationError("El archivo no es una imagen que se pueda leer.")
    image = ImageOps.exif_transpose(image)
    has_alpha = image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)
    image = image.convert("RGBA" if has_alpha else "RGB")
    if max(image.size) > MAX_SIDE_PX:
        image.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX), Image.Resampling.LANCZOS)
    output = BytesIO()
    # PNG también para PNG/GIF de origen (sin pérdida: un logo plano no debe
    # sacar artefactos de JPEG); JPEG solo para fotos y similares.
    if has_alpha or source_format in ("PNG", "GIF"):
        image.save(output, format="PNG", optimize=True)
        ext = "png"
    else:
        image.save(output, format="JPEG", quality=90)
        ext = "jpg"
    return ContentFile(output.getvalue(), name=f"{uuid.uuid4().hex}.{ext}")


def image_file(image_id):
    """El archivo de la imagen ``image_id`` (activa o no: lo borrado sigue
    imprimiéndose en lo que ya la usa), o ``None`` si no existe."""
    from .models import LabelImage

    row = LabelImage.objects.filter(pk=image_id).first() if isinstance(image_id, int) else None
    if row is None or not row.image:
        return None
    try:
        return row.image.open("rb")
    except (FileNotFoundError, OSError):
        return None


def resolve_source(shape, logo_file):
    """El archivo que dibuja una forma imagen: ``"logo"`` → el logo del
    rótulo; un id → su ``LabelImage``. ``None`` si no hay."""
    image = shape.get("image")
    if image == "logo":
        if logo_file is None:
            return None
        try:
            logo_file.seek(0)
        except (AttributeError, OSError, ValueError):
            pass
        return logo_file
    return image_file(image)


def prepare(source, box_w_px, box_h_px, fit, circle, radius_px):
    """La imagen lista para dibujar: RGBA de exactamente ``box_w_px ×
    box_h_px``. ``contain`` la escala entera y centrada (transparente
    alrededor), ``cover`` hasta llenar la caja recortando el centro;
    ``circle`` deja el mayor círculo centrado y ``radius_px`` redondea las
    esquinas de la caja (se ignora con ``circle``)."""
    image = Image.open(source)
    image.load()
    image = ImageOps.exif_transpose(image).convert("RGBA")
    ratio_w, ratio_h = box_w_px / image.width, box_h_px / image.height
    scale = max(ratio_w, ratio_h) if fit == "cover" else min(ratio_w, ratio_h)
    new_size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    image = image.resize(new_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (box_w_px, box_h_px), (0, 0, 0, 0))
    canvas.paste(image, ((box_w_px - new_size[0]) // 2, (box_h_px - new_size[1]) // 2))
    mask = None
    if circle:
        side = min(box_w_px, box_h_px)
        left, top = (box_w_px - side) // 2, (box_h_px - side) // 2
        mask = Image.new("L", (box_w_px, box_h_px), 0)
        ImageDraw.Draw(mask).ellipse((left, top, left + side - 1, top + side - 1), fill=255)
    elif radius_px > 0:
        mask = Image.new("L", (box_w_px, box_h_px), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, box_w_px - 1, box_h_px - 1), radius=min(radius_px, box_w_px / 2, box_h_px / 2), fill=255
        )
    if mask is not None:
        alpha = Image.composite(canvas.getchannel("A"), Image.new("L", canvas.size, 0), mask)
        canvas.putalpha(alpha)
    return canvas


def allowed_image_ids(user, extra_designs=()):
    """Los ids de imagen que ``user`` puede usar en un diseño: las propias de
    su comercio (activas o no; sin comercio, ninguna), las que usan las
    plantillas públicas o del sistema que puede leer y las de
    ``extra_designs`` (el diseño que ya tiene lo que está editando)."""
    from django.db.models import Q

    from apps.accounts.teams import NoTeam, data_owner

    from .design_shapes import image_ids
    from .models import LabelImage, LabelTemplate

    try:
        own = set(LabelImage.objects.filter(owner=data_owner(user)).values_list("pk", flat=True))
    except NoTeam:
        own = set()
    allowed = set(own)
    public = LabelTemplate.objects.filter(Q(is_public=True) | Q(owner__isnull=True), is_active=True)
    for design in public.values_list("design", flat=True):
        allowed |= image_ids(design)
    for design in extra_designs:
        allowed |= image_ids(design)
    return allowed
