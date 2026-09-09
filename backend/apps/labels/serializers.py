"""Serializers de plantillas y rótulos.

El ``design``/``fields`` del editor (``frontend/pedidos/diseñorotulos.html``)
se valida de verdad acá, no se acepta crudo: ver ``KNOWN_DESIGN_FIELDS`` y
``validate_design``. Logo y miniatura llegan hoy del editor como data URL
base64 (``ImageOrDataUrlField`` los decodifica a ``ImageField``), pero
también se acepta un archivo real vía ``multipart/form-data``.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from decimal import Decimal
from io import BytesIO

from django.core.files.base import ContentFile
from django.db.models import Q
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers

from apps.accounts.permissions_map import get_effective_role
from apps.orders.models import Order

from .models import Label, LabelTemplate

# Campos de texto simple del diseño: solo left/top + un "text" opcional.
TEXT_DESIGN_FIELDS = {
    "remitente",
    "destinatario",
    "domicilio",
    "cp",
    "localidad",
    "pedido",
}

# Campos válidos del diseño del rótulo (deben coincidir con el editor).
KNOWN_DESIGN_FIELDS = TEXT_DESIGN_FIELDS | {"logo", "qr", "barcode"}

DATA_URL_RE = re.compile(r"^data:image/(?P<ext>[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$")

# Rangos válidos de tamaño de código, en CENTÍMETROS (ver
# apps.labels.rendering: un código escaneable necesita un tamaño físico
# mínimo, por eso no se mide en porcentaje del rótulo como left/top).
QR_SIZE_MIN, QR_SIZE_MAX = 1, 10
BARCODE_WIDTH_MIN, BARCODE_WIDTH_MAX = 2, 20
BARCODE_HEIGHT_MIN, BARCODE_HEIGHT_MAX = 0.5, 5
VALID_BARCODE_SYMBOLOGIES = {"code128", "ean13"}


def _validate_number_range(value, field_label, minimum, maximum):
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not (minimum <= value <= maximum)
    ):
        raise serializers.ValidationError(
            f"'{field_label}' debe ser un número entre {minimum} y {maximum}."
        )


def _validate_qr_entry(key, entry):
    extra_keys = set(entry) - {"left", "top", "size", "data"}
    if extra_keys:
        raise serializers.ValidationError(
            f"'{key}' no admite: {', '.join(sorted(extra_keys))}."
        )
    if "size" in entry:
        _validate_number_range(entry["size"], f"{key}.size", QR_SIZE_MIN, QR_SIZE_MAX)
    data = entry.get("data")
    if data is not None and not isinstance(data, str):
        raise serializers.ValidationError(f"'{key}.data' debe ser un texto.")


def _validate_barcode_entry(key, entry):
    extra_keys = set(entry) - {"left", "top", "width", "height", "symbology", "data", "show_text"}
    if extra_keys:
        raise serializers.ValidationError(
            f"'{key}' no admite: {', '.join(sorted(extra_keys))}."
        )
    if "width" in entry:
        _validate_number_range(entry["width"], f"{key}.width", BARCODE_WIDTH_MIN, BARCODE_WIDTH_MAX)
    if "height" in entry:
        _validate_number_range(
            entry["height"], f"{key}.height", BARCODE_HEIGHT_MIN, BARCODE_HEIGHT_MAX
        )
    symbology = entry.get("symbology")
    if symbology is not None and symbology not in VALID_BARCODE_SYMBOLOGIES:
        raise serializers.ValidationError(
            f"'{key}.symbology' debe ser uno de: {', '.join(sorted(VALID_BARCODE_SYMBOLOGIES))}."
        )
    data = entry.get("data")
    if data is not None and not isinstance(data, str):
        raise serializers.ValidationError(f"'{key}.data' debe ser un texto.")
    show_text = entry.get("show_text")
    if show_text is not None and not isinstance(show_text, bool):
        raise serializers.ValidationError(f"'{key}.show_text' debe ser verdadero/falso.")


def validate_design(value):
    """Valida el bloque ``design``/``fields`` del editor.

    Debe ser un dict cuyas claves estén en ``KNOWN_DESIGN_FIELDS`` y cuyos
    valores tengan ``{"left": <0-100>, "top": <0-100>}`` más, según el
    campo:

    - Campos de texto (``TEXT_DESIGN_FIELDS``): ``"text"`` opcional.
    - ``logo``: nada más (left/top).
    - ``qr``: ``"size"`` opcional en cm (1-10) y ``"data"`` opcional (texto,
      admite marcadores ``{{...}}``) — un diseño viejo sin esas dos claves
      sigue siendo válido, ``apps.labels.rendering`` aplica los defaults.
    - ``barcode``: ``"width"``/``"height"`` opcionales en cm (2-20 / 0.5-5),
      ``"symbology"`` opcional (``code128``/``ean13``), ``"data"`` opcional
      y ``"show_text"`` opcional (booleano).
    """
    if not isinstance(value, dict):
        raise serializers.ValidationError(
            "El diseño debe ser un objeto con la posición de cada campo."
        )
    for key, entry in value.items():
        if key not in KNOWN_DESIGN_FIELDS:
            raise serializers.ValidationError(f"'{key}' no es un campo de rótulo reconocido.")
        if not isinstance(entry, dict):
            raise serializers.ValidationError(f"'{key}' debe ser un objeto con left/top.")
        for coord in ("left", "top"):
            coord_value = entry.get(coord)
            if (
                not isinstance(coord_value, (int, float))
                or isinstance(coord_value, bool)
                or not (0 <= coord_value <= 100)
            ):
                raise serializers.ValidationError(
                    f"'{key}.{coord}' debe ser un número entre 0 y 100."
                )

        if key in TEXT_DESIGN_FIELDS:
            text = entry.get("text")
            if text is not None and not isinstance(text, str):
                raise serializers.ValidationError(f"'{key}.text' debe ser un texto.")
        elif key == "qr":
            _validate_qr_entry(key, entry)
        elif key == "barcode":
            _validate_barcode_entry(key, entry)
        # "logo": solo left/top, nada más que validar acá.
    return value


def _decode_data_url(value, *, max_bytes, field_label):
    match = DATA_URL_RE.match(value.strip())
    if not match:
        raise serializers.ValidationError(f"{field_label}: formato de imagen inválido.")
    ext = match.group("ext").lower()
    if ext == "jpg":
        ext = "jpeg"
    try:
        raw = base64.b64decode(match.group("data"), validate=True)
    except (binascii.Error, ValueError):
        raise serializers.ValidationError(f"{field_label}: no se pudo decodificar la imagen.")
    if not raw:
        raise serializers.ValidationError(f"{field_label}: la imagen está vacía.")
    if len(raw) > max_bytes:
        raise serializers.ValidationError(
            f"{field_label}: supera el tamaño máximo permitido ({max_bytes // 1024} KB)."
        )
    try:
        image = Image.open(BytesIO(raw))
        image.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise serializers.ValidationError(f"{field_label}: el archivo no es una imagen válida.")
    return ContentFile(raw, name=f"{uuid.uuid4().hex}.{ext}")


class ImageOrDataUrlField(serializers.ImageField):
    """Acepta un archivo real (multipart) o un string data URL base64 (lo
    que produce hoy el editor) y siempre guarda en ``ImageField``."""

    def __init__(self, *args, max_bytes=2 * 1024 * 1024, field_label="Imagen", **kwargs):
        self.max_bytes = max_bytes
        self.field_label = field_label
        super().__init__(*args, **kwargs)

    def to_internal_value(self, data):
        if isinstance(data, str):
            if not data.strip():
                return None
            return _decode_data_url(data, max_bytes=self.max_bytes, field_label=self.field_label)
        return super().to_internal_value(data)


class LabelTemplateSerializer(serializers.ModelSerializer):
    owner_email = serializers.EmailField(source="owner.email", read_only=True, default=None)
    preview = ImageOrDataUrlField(
        required=False, allow_null=True, max_bytes=300 * 1024, field_label="Miniatura"
    )

    class Meta:
        model = LabelTemplate
        fields = [
            "id",
            "name",
            "description",
            "owner",
            "owner_email",
            "is_public",
            "width_cm",
            "height_cm",
            "design",
            "preview",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "owner", "owner_email", "created_at", "updated_at"]
        extra_kwargs = {
            "width_cm": {"min_value": Decimal("5"), "max_value": Decimal("30")},
            "height_cm": {"min_value": Decimal("5"), "max_value": Decimal("40")},
        }

    def validate_design(self, value):
        return validate_design(value)


class LabelSerializer(serializers.ModelSerializer):
    """CRUD del rótulo propio del usuario autenticado.

    ``template``/``order`` solo se pueden elegir entre los propios (o
    públicos, para plantillas) salvo que el usuario sea admin.
    """

    logo = ImageOrDataUrlField(
        required=False, allow_null=True, max_bytes=2 * 1024 * 1024, field_label="Logo"
    )
    thumbnail = ImageOrDataUrlField(
        required=False, allow_null=True, max_bytes=300 * 1024, field_label="Miniatura"
    )

    class Meta:
        model = Label
        fields = [
            "id",
            "template",
            "name",
            "client",
            "order",
            "width_cm",
            "height_cm",
            "design",
            "logo",
            "thumbnail",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_active", "created_at", "updated_at"]
        extra_kwargs = {
            "width_cm": {"min_value": Decimal("5"), "max_value": Decimal("30")},
            "height_cm": {"min_value": Decimal("5"), "max_value": Decimal("40")},
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        user = getattr(request, "user", None) if request is not None else None
        if user is not None and user.is_authenticated:
            # Un usuario solo puede elegir plantillas propias o públicas.
            self.fields["template"].queryset = LabelTemplate.objects.filter(
                Q(is_public=True) | Q(owner=user)
            )
            # Un usuario solo puede colgar el rótulo de un pedido PROPIO; el
            # admin puede saltarse esa restricción.
            if get_effective_role(user) == "admin":
                self.fields["order"].queryset = Order.objects.all()
            else:
                self.fields["order"].queryset = Order.objects.filter(user=user)

    def validate_design(self, value):
        return validate_design(value)


class AdminLabelSerializer(serializers.ModelSerializer):
    """Lectura de rótulos de TODOS los usuarios (panel admin, ``labels.view_all``).
    Solo lectura, igual criterio que ``AdminOrderSerializer``."""

    user_email = serializers.EmailField(source="user.email", read_only=True)
    template_name = serializers.CharField(source="template.name", read_only=True, default=None)

    class Meta:
        model = Label
        fields = [
            "id",
            "user",
            "user_email",
            "template",
            "template_name",
            "name",
            "client",
            "order",
            "width_cm",
            "height_cm",
            "logo",
            "thumbnail",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
