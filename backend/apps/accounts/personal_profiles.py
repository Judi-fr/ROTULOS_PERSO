"""El perfil personal de cada persona (``PersonalProfile``): nombre para
mostrar, teléfono, puesto y foto propia. Es lo que ve su equipo (miembros y
actividad, ``team_views.py``) y lo que muestra la barra de arriba."""

from io import BytesIO

from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError
from rest_framework import serializers

from .models import personal_profile_for

# La foto se guarda cuadrada y chica: se muestra como avatar (a lo sumo
# ~100 px), así que más resolución solo ocuparía disco.
AVATAR_SIZE_PX = 256
AVATAR_MAX_BYTES = 2 * 1024 * 1024


def normalize_photo(uploaded):
    """La imagen subida, recortada al cuadrado del centro, achicada a
    ``AVATAR_SIZE_PX`` y guardada como JPEG (sin los datos EXIF del teléfono,
    que pueden traer dónde se sacó)."""
    try:
        uploaded.seek(0)
        image = Image.open(uploaded)
        image.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise serializers.ValidationError("La foto no es una imagen que se pueda leer.")
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")
    image = ImageOps.fit(image, (AVATAR_SIZE_PX, AVATAR_SIZE_PX), Image.Resampling.LANCZOS)
    output = BytesIO()
    image.save(output, format="JPEG", quality=85)
    return ContentFile(output.getvalue(), name="avatar.jpg")


def full_name(user):
    return " ".join(filter(None, [user.first_name, user.last_name])).strip()


_UNREAD = object()


def display_name(user, profile=_UNREAD):
    """Cómo se lo nombra en la app: el nombre que eligió, si no nombre y
    apellido, si no el email. ``profile`` evita releerlo (``None`` = no tiene)."""
    if profile is _UNREAD:
        profile = personal_profile_for(user)
    return (profile and profile.preferred_name) or full_name(user) or user.email or f"Usuario {user.pk}"


def photo_url(profile, request=None):
    if not profile or not profile.photo:
        return None
    url = profile.photo.url
    return request.build_absolute_uri(url) if request is not None else url


def public_fields(user, request=None):
    """Lo que el resto del equipo ve de una persona."""
    profile = personal_profile_for(user)
    return {
        "display_name": display_name(user, profile),
        "job_title": profile.job_title if profile else "",
        "phone": profile.phone if profile else "",
        "photo": photo_url(profile, request),
    }
