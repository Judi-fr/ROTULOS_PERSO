"""Serializers de plantillas y rótulos.

El ``design``/``fields`` del editor (``frontend/editor_rotulos.html``)
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

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.core.files.base import ContentFile
from django.db.models import Q
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers

from apps.accounts.permissions_map import get_effective_role, user_has_permission
from apps.accounts.teams import contact_email as owner_contact_email
from apps.orders.models import Order
from apps.accounts.teams import data_owner, order_scope_q

from .design_shapes import image_ids, shape_targets, validate_palette, validate_shapes
from .text_style import validate_text_typography
from .styles import validate_style
from .models import ElementLayout, Label, LabelImage, LabelTemplate, LabelTemplateVersion, LayoutElement, LayoutVariable
from . import label_images

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
# apps.labels.label_rendering: un código escaneable necesita un tamaño físico
# mínimo, por eso no se mide en porcentaje del rótulo como left/top).
QR_SIZE_MIN, QR_SIZE_MAX = 1, 10
BARCODE_WIDTH_MIN, BARCODE_WIDTH_MAX = 2, 20
BARCODE_HEIGHT_MIN, BARCODE_HEIGHT_MAX = 0.5, 5
VALID_BARCODE_SYMBOLOGIES = {"code128", "ean13"}

# Reglas condicionales de contenido (Historia 28, ver
# apps.labels.label_rendering.apply_design_rules, que las EVALÚA con esta misma
# forma).
VALID_RULE_OPS = {
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "empty",
    "not_empty",
    "in",
    "gt",
    "lt",
}
VALID_RULE_ACTIONS = {"hide", "show", "set_text", "move"}
MAX_DESIGN_RULES = 50

# Estilo opcional de un texto (ver apps.labels.label_rendering._draw_text_entry).
FONT_SIZE_MIN, FONT_SIZE_MAX = 5, 40
VALID_TEXT_ALIGNS = {"left", "center", "right"}
# Opciones de 2026-10-21: texto invertido (blanco sobre negro), recuadro propio,
# una etiqueta fija delante del dato ("CP:") y texto vertical.
VALID_TEXT_ROTATIONS = {0, 90, 270}
MAX_PREFIX_LENGTH = 40
MAX_EXTRA_TEXTS = 30
MAX_DESIGN_LINES = 20

# Decoración del diseño: no son campos posicionados, así que una regla no
# puede apuntarles.
DECORATION_KEYS = {"texts", "lines", "border", "shapes", "palette"}

# Claves que solo usa el editor y el render ignora: ``locked`` es la lista de
# campos bloqueados (no se arrastran por accidente). Una regla tampoco puede
# apuntarles.
EDITOR_ONLY_KEYS = {"locked"}


def _validate_text_style(label, entry, palette=None):
    text = entry.get("text")
    if text is not None and not isinstance(text, str):
        raise serializers.ValidationError(f"'{label}.text' debe ser un texto.")
    if "font_size" in entry:
        _validate_number_range(entry["font_size"], f"{label}.font_size", FONT_SIZE_MIN, FONT_SIZE_MAX)
    if "width" in entry:
        _validate_number_range(entry["width"], f"{label}.width", 1, 100)
    for flag in ("bold", "hide_if_empty", "shrink_to_fit", "locked", "invert", "frame", "prefix_bold"):
        if flag in entry and not isinstance(entry[flag], bool):
            raise serializers.ValidationError(f"'{label}.{flag}' debe ser verdadero/falso.")
    if "prefix" in entry:
        prefix = entry["prefix"]
        if not isinstance(prefix, str) or len(prefix) > MAX_PREFIX_LENGTH:
            raise serializers.ValidationError(
                f"'{label}.prefix' debe ser un texto de hasta {MAX_PREFIX_LENGTH} caracteres."
            )
    if "rotate" in entry and (
        isinstance(entry["rotate"], bool) or entry["rotate"] not in VALID_TEXT_ROTATIONS
    ):
        raise serializers.ValidationError(f"'{label}.rotate' debe ser 0, 90 o 270.")
    if "align" in entry and entry["align"] not in VALID_TEXT_ALIGNS:
        raise serializers.ValidationError(
            f"'{label}.align' debe ser uno de: {', '.join(sorted(VALID_TEXT_ALIGNS))}."
        )
    # Tipografía y color (fase 2 del editor, text_style.py).
    validate_text_typography(entry, label, palette)


def _validate_position(label, entry):
    for coord in ("left", "top"):
        _validate_number_range(entry.get(coord), f"{label}.{coord}", 0, 100)


def _validate_extra_texts(value, palette=None):
    if not isinstance(value, list):
        raise serializers.ValidationError("'texts' debe ser una lista.")
    if len(value) > MAX_EXTRA_TEXTS:
        raise serializers.ValidationError(f"'texts' admite como máximo {MAX_EXTRA_TEXTS} textos.")
    for index, entry in enumerate(value):
        label = f"texts[{index}]"
        if not isinstance(entry, dict):
            raise serializers.ValidationError(f"'{label}' debe ser un objeto.")
        _validate_position(label, entry)
        if not isinstance(entry.get("text"), str):
            raise serializers.ValidationError(f"'{label}.text' es obligatorio y debe ser un texto.")
        _validate_text_style(label, entry, palette)


def _validate_lines(value):
    if not isinstance(value, list):
        raise serializers.ValidationError("'lines' debe ser una lista.")
    if len(value) > MAX_DESIGN_LINES:
        raise serializers.ValidationError(f"'lines' admite como máximo {MAX_DESIGN_LINES} líneas.")
    for index, entry in enumerate(value):
        label = f"lines[{index}]"
        if not isinstance(entry, dict):
            raise serializers.ValidationError(f"'{label}' debe ser un objeto.")
        _validate_number_range(entry.get("top"), f"{label}.top", 0, 100)
        left, right = entry.get("left", 4), entry.get("right", 96)
        _validate_number_range(left, f"{label}.left", 0, 100)
        _validate_number_range(right, f"{label}.right", 0, 100)
        if left >= right:
            raise serializers.ValidationError(f"'{label}.left' debe ser menor que '{label}.right'.")
        for flag in ("dashed", "locked"):
            if flag in entry and not isinstance(entry[flag], bool):
                raise serializers.ValidationError(f"'{label}.{flag}' debe ser verdadero/falso.")


def _validate_locked(value):
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise serializers.ValidationError("'locked' debe ser una lista de nombres de campo.")
    unknown = set(value) - KNOWN_DESIGN_FIELDS
    if unknown:
        raise serializers.ValidationError(
            f"'locked' tiene campos que no existen: {', '.join(sorted(unknown))}."
        )


def _validate_border(value):
    if not isinstance(value, dict):
        raise serializers.ValidationError("'border' debe ser un objeto.")
    if "dashed" in value and not isinstance(value["dashed"], bool):
        raise serializers.ValidationError("'border.dashed' debe ser verdadero/falso.")


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


def _validate_design_rule(rule, index, known_targets):
    if not isinstance(rule, dict):
        raise serializers.ValidationError(f"'rules[{index}]' debe ser un objeto.")
    extra_keys = set(rule) - {"when", "then"}
    if extra_keys:
        raise serializers.ValidationError(
            f"'rules[{index}]' no admite: {', '.join(sorted(extra_keys))}."
        )

    when = rule.get("when")
    if not isinstance(when, dict):
        raise serializers.ValidationError(f"'rules[{index}].when' es obligatorio.")
    field = when.get("field")
    if not isinstance(field, str) or not field.strip():
        raise serializers.ValidationError(f"'rules[{index}].when.field' es obligatorio.")
    op = when.get("op")
    if op not in VALID_RULE_OPS:
        raise serializers.ValidationError(
            f"'rules[{index}].when.op' debe ser uno de: {', '.join(sorted(VALID_RULE_OPS))}."
        )
    if op == "in" and not isinstance(when.get("value"), list):
        raise serializers.ValidationError(
            f"'rules[{index}].when.value' debe ser una lista para el operador 'in'."
        )

    then = rule.get("then")
    if not isinstance(then, dict):
        raise serializers.ValidationError(f"'rules[{index}].then' es obligatorio.")
    action = then.get("action")
    if action not in VALID_RULE_ACTIONS:
        raise serializers.ValidationError(
            f"'rules[{index}].then.action' debe ser uno de: "
            f"{', '.join(sorted(VALID_RULE_ACTIONS))}."
        )
    target = then.get("target")
    if target not in known_targets:
        raise serializers.ValidationError(
            f"'rules[{index}].then.target' debe ser un campo existente del diseño."
        )
    if action == "set_text" and not isinstance(then.get("value", ""), str):
        raise serializers.ValidationError(f"'rules[{index}].then.value' debe ser un texto.")
    if action == "move":
        for coord in ("left", "top"):
            coord_value = then.get(coord)
            if (
                not isinstance(coord_value, (int, float))
                or isinstance(coord_value, bool)
                or not (0 <= coord_value <= 100)
            ):
                raise serializers.ValidationError(
                    f"'rules[{index}].then.{coord}' debe ser un número entre 0 y 100."
                )


def check_design_images(value, serializer, extra_designs=()):
    """Las imágenes del diseño tienen que poder usarse (``label_images.
    allowed_image_ids``: las del comercio, las de plantillas públicas y las
    que el diseño que se edita ya tiene). Va en el serializer y no en
    ``validate_design``, que es puro: necesita la petición."""
    ids = image_ids(value)
    request = serializer.context.get("request")
    if not ids or request is None:
        return value
    if callable(extra_designs):
        extra_designs = extra_designs()  # perezoso: solo si el diseño usa imágenes
    if ids - label_images.allowed_image_ids(request.user, extra_designs):
        raise serializers.ValidationError("Una de las imágenes del diseño no existe en tu comercio.")
    return value


def validate_design(value):
    """Valida el bloque ``design``/``fields`` del editor.

    Debe ser un dict cuyas claves estén en ``KNOWN_DESIGN_FIELDS`` y cuyos
    valores tengan ``{"left": <0-100>, "top": <0-100>}`` más, según el
    campo:

    - Campos de texto (``TEXT_DESIGN_FIELDS``): ``"text"`` opcional.
    - ``logo``: nada más (left/top).
    - ``qr``: ``"size"`` opcional en cm (1-10) y ``"data"`` opcional (texto,
      admite marcadores ``{{...}}``) — un diseño viejo sin esas dos claves
      sigue siendo válido, ``apps.labels.label_rendering`` aplica los defaults.
    - ``barcode``: ``"width"``/``"height"`` opcionales en cm (2-20 / 0.5-5),
      ``"symbology"`` opcional (``code128``/``ean13``), ``"data"`` opcional
      y ``"show_text"`` opcional (booleano).

    Estilo opcional de los textos: ``font_size`` (pt), ``bold``, ``align``
    (left/center/right), ``width`` (% del rótulo), ``hide_if_empty``,
    ``shrink_to_fit``, ``invert`` (blanco sobre negro), ``frame`` (recuadro),
    ``prefix``/``prefix_bold`` (etiqueta fija delante del dato) y ``rotate``
    (0, 90 = de arriba hacia abajo, 270 = de abajo hacia arriba).
    Decoración opcional: ``texts`` (textos extra con la misma forma),
    ``lines`` (separadores horizontales ``{top, left, right, dashed}``) y
    ``border`` (``{dashed}``, borde de corte). Un texto extra o una línea
    pueden llevar ``locked`` (booleano).

    ``"locked"`` (opcional): lista de campos bloqueados en el editor. Solo la
    usa el editor; el render la ignora.

    Además, ``"rules"`` (opcional, lista, Historia 28) — reglas
    condicionales que ``apps.labels.label_rendering.apply_design_rules`` evalúa
    en el render: cada ítem necesita ``when``/``then`` con las claves
    obligatorias, ``when.op``/``then.action`` dentro de los valores
    permitidos, ``then.target`` una clave que exista en este mismo
    ``design``, y ``then.left``/``then.top`` (para ``action: "move"``) en
    0-100. Máximo ``MAX_DESIGN_RULES`` reglas.
    """
    if not isinstance(value, dict):
        raise serializers.ValidationError(
            "El diseño debe ser un objeto con la posición de cada campo."
        )

    rules = value.get("rules", [])
    if not isinstance(rules, list):
        raise serializers.ValidationError("'rules' debe ser una lista.")
    if len(rules) > MAX_DESIGN_RULES:
        raise serializers.ValidationError(
            f"'rules' admite como máximo {MAX_DESIGN_RULES} reglas."
        )
    known_targets = (set(value.keys()) - {"rules"} - DECORATION_KEYS - EDITOR_ONLY_KEYS) | shape_targets(value)
    palette = value.get("palette")

    for key, entry in value.items():
        if key == "rules":
            continue
        if key == "locked":
            _validate_locked(entry)
            continue
        if key == "texts":
            _validate_extra_texts(entry, palette if isinstance(palette, dict) else None)
            continue
        if key == "lines":
            _validate_lines(entry)
            continue
        if key == "border":
            _validate_border(entry)
            continue
        # Formas y paleta (fase 1 del editor): ver design_shapes.py.
        if key == "palette":
            validate_palette(entry)
            continue
        if key == "shapes":
            validate_shapes(entry, palette if isinstance(palette, dict) else None)
            continue
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
            _validate_text_style(key, entry, palette if isinstance(palette, dict) else None)
        elif key == "qr":
            _validate_qr_entry(key, entry)
        elif key == "barcode":
            _validate_barcode_entry(key, entry)
        # "logo": solo left/top, nada más que validar acá.

    for index, rule in enumerate(rules):
        _validate_design_rule(rule, index, known_targets)

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
    except Image.DecompressionBombError:
        raise serializers.ValidationError(f"{field_label}: la imagen es demasiado grande (máximo 40 megapíxeles).")
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
    # El mail de la persona dueña del comercio (los datos cuelgan de su
    # usuario de datos, que no es una persona).
    owner_email = serializers.SerializerMethodField()
    preview = ImageOrDataUrlField(
        required=False, allow_null=True, max_bytes=300 * 1024, field_label="Miniatura"
    )
    # Número de la última versión (template_versions.py). El viewset lo anota
    # en la consulta; una plantilla recién creada lo lee de la base.
    current_version = serializers.SerializerMethodField()
    # Si quien pregunta puede guardar cambios en ella: la misma regla que
    # LabelTemplateViewSet.perform_update, para que el frontend no la copie.
    can_edit = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()
    # Nota de la versión que deja este guardado ("cambié el logo"). Opcional.
    version_note = serializers.CharField(
        write_only=True, required=False, allow_blank=True, max_length=200
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
            "gallery_order",
            "current_version",
            "can_edit",
            "can_delete",
            "version_note",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "owner", "owner_email", "gallery_order", "created_at", "updated_at"]
        extra_kwargs = {
            "width_cm": {"min_value": Decimal("5"), "max_value": Decimal("30")},
            "height_cm": {"min_value": Decimal("5"), "max_value": Decimal("40")},
        }

    def get_owner_email(self, obj):
        return owner_contact_email(getattr(obj, "owner", None)) or None

    def validate_design(self, value):
        def extra():
            if self.instance is None:
                return []
            # Lo que la plantilla ya tiene (y su historial, de donde se
            # restaura) se puede volver a guardar tal cual.
            return [self.instance.design] + list(self.instance.versions.values_list("design", flat=True))

        return check_design_images(validate_design(value), self, extra)

    def get_current_version(self, obj):
        annotated = getattr(obj, "current_version", None)
        if annotated is not None:
            return annotated
        last = obj.versions.order_by("-number").values_list("number", flat=True).first()
        return last

    def _may(self, obj, permission):
        # Lo mismo que la vista: el permiso de la acción (migración 0029) y,
        # sin labels.manage_templates, que sea una plantilla propia privada.
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return False
        if user_has_permission(user, "labels.manage_templates"):
            return True
        if not user_has_permission(user, permission):
            return False
        return obj.owner_id == data_owner(user).id and not obj.is_public

    def get_can_edit(self, obj):
        return self._may(obj, "labels.template_edit")

    def get_can_delete(self, obj):
        return self._may(obj, "labels.template_delete")

    def create(self, validated_data):
        validated_data.pop("version_note", None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        validated_data.pop("version_note", None)
        return super().update(instance, validated_data)


class LabelTemplateVersionSerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True, default=None)

    class Meta:
        model = LabelTemplateVersion
        fields = [
            "number",
            "name",
            "width_cm",
            "height_cm",
            "design",
            "note",
            "restored_from",
            "created_by_email",
            "created_at",
        ]
        read_only_fields = fields


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
            # Un usuario solo puede elegir plantillas propias o públicas,
            # y solo si siguen activas (una archivada no se puede volver a
            # usar en un rótulo nuevo, aunque los ya creados con ella la
            # sigan referenciando).
            self.fields["template"].queryset = LabelTemplate.objects.filter(
                Q(is_public=True) | Q(owner=data_owner(user)), is_active=True
            )
            # Un usuario solo puede colgar el rótulo de un pedido PROPIO; el
            # admin puede saltarse esa restricción.
            if get_effective_role(user) == "admin":
                self.fields["order"].queryset = Order.objects.all()
            else:
                self.fields["order"].queryset = Order.objects.filter(order_scope_q(user), user=data_owner(user))

    def validate_design(self, value):
        extra = [self.instance.design] if self.instance is not None else []
        return check_design_images(validate_design(value), self, extra)


class AdminLabelSerializer(serializers.ModelSerializer):
    """Lectura de rótulos de TODOS los usuarios (panel admin, ``labels.view_all``).
    Solo lectura, igual criterio que ``AdminOrderSerializer``."""

    # El mail de la persona dueña del comercio (los datos cuelgan de su
    # usuario de datos, que no es una persona).
    user_email = serializers.SerializerMethodField()
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

    def get_user_email(self, obj):
        return owner_contact_email(getattr(obj, "user", None)) or None

# ---------------------------------------------------------------------------
# ElementLayout/LayoutElement/LayoutVariable: plantillas por elementos +
# catálogo de variables. Conviven con los serializers de Label/LabelTemplate
# (concepto distinto, no se reemplazan entre sí — ver apps/labels/models.py).
# ---------------------------------------------------------------------------


class LayoutVariableSerializer(serializers.ModelSerializer):
    """Una variable del catálogo.

    ``is_system`` es de solo lectura: las variables del sistema las siembra
    una migración, no se crean por API. ``created_by`` lo fija la vista con
    el usuario autenticado.
    """

    created_by_email = serializers.EmailField(
        source="created_by.email", read_only=True, default=None
    )

    class Meta:
        model = LayoutVariable
        fields = [
            "id",
            "code",
            "label",
            "description",
            "data_type",
            "is_active",
            "is_system",
            "order",
            "created_by",
            "created_by_email",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "is_system",
            "created_by",
            "created_by_email",
            "created_at",
        ]

    def validate(self, attrs):
        if self.instance and self.instance.is_system:
            new_code = attrs.get("code", self.instance.code)
            if new_code != self.instance.code:
                raise serializers.ValidationError(
                    {"code": "No se puede cambiar el código de una "
                             "variable del sistema."}
                )
        return attrs


class LayoutElementSerializer(serializers.ModelSerializer):
    """Un elemento posicionado dentro de una plantilla."""

    variable = serializers.SlugRelatedField(
        slug_field="code",
        queryset=LayoutVariable.objects.all(),
        allow_null=True,
        required=False,
    )
    variable_display = serializers.SerializerMethodField()
    variable_data_type = serializers.SerializerMethodField()

    class Meta:
        model = LayoutElement
        fields = [
            "id",
            "element_type",
            "variable",
            "variable_display",
            "variable_data_type",
            "content",
            "x_mm",
            "y_mm",
            "width_mm",
            "height_mm",
            "style",
            "order",
        ]

    def get_variable_display(self, obj):
        """Etiqueta legible de la variable, o ``None`` si el elemento no usa una."""
        return obj.variable.label if obj.variable_id else None

    def get_variable_data_type(self, obj):
        """Cómo debe dibujarse la variable (texto, qr, imagen…)."""
        return obj.variable.data_type if obj.variable_id else None

    def validate_style(self, value):
        try:
            validate_style(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return value

    def validate(self, attrs):
        try:
            LayoutElement(**attrs).clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict) from exc
        return attrs


class ElementLayoutSerializer(serializers.ModelSerializer):
    """Plantilla con sus elementos anidados."""

    elements = LayoutElementSerializer(many=True, required=False)
    created_by_email = serializers.EmailField(
        source="created_by.email", read_only=True, default=None
    )
    width_px = serializers.IntegerField(read_only=True)
    height_px = serializers.IntegerField(read_only=True)

    class Meta:
        model = ElementLayout
        fields = [
            "id",
            "name",
            "description",
            "width_mm",
            "height_mm",
            "dpi",
            "orientation",
            "metadata",
            "is_active",
            "elements",
            "width_px",
            "height_px",
            "created_by",
            "created_by_email",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_by",
            "created_by_email",
            "width_px",
            "height_px",
            "created_at",
            "updated_at",
        ]

    def _create_elements(self, layout, elements):
        LayoutElement.objects.bulk_create(
            [LayoutElement(layout=layout, **elem) for elem in elements]
        )

    @transaction.atomic
    def create(self, validated_data):
        elements = validated_data.pop("elements", [])
        layout = ElementLayout.objects.create(**validated_data)
        self._create_elements(layout, elements)
        return layout

    @transaction.atomic
    def update(self, instance, validated_data):
        elements = validated_data.pop("elements", None)

        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()

        if elements is not None:
            instance.elements.all().delete()
            self._create_elements(instance, elements)

        return instance


class RenderElementLayoutSerializer(serializers.Serializer):
    """Cuerpo de ``POST /element-layouts/<id>/render/``."""

    format = serializers.ChoiceField(choices=["pdf", "png"], default="pdf")
    data = serializers.DictField(required=False, allow_null=True)
    batch = serializers.ListField(
        child=serializers.DictField(), required=False, allow_empty=False
    )
    dpi = serializers.IntegerField(required=False, min_value=10, max_value=1200)

    def validate(self, attrs):
        if attrs.get("batch") and attrs.get("data"):
            raise serializers.ValidationError(
                "Mandá 'data' para un rótulo o 'batch' para varios, no ambos."
            )
        if attrs.get("batch") and attrs.get("format", "pdf") != "pdf":
            raise serializers.ValidationError(
                {"batch": "Un lote solo se puede generar en PDF: un PNG es una "
                          "sola imagen y no tiene páginas."}
            )
        return attrs


class LabelImageSerializer(serializers.ModelSerializer):
    """Lo que ve el editor de una imagen de la biblioteca. La subida acepta
    archivo o data URL y la guarda normalizada."""

    url = serializers.SerializerMethodField()
    image = ImageOrDataUrlField(write_only=True, field_label="La imagen")

    class Meta:
        model = LabelImage
        fields = ["id", "url", "name", "width_px", "height_px", "created_at", "image"]
        read_only_fields = ["id", "url", "width_px", "height_px", "created_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Leída acá y no al definir la clase, para que valga lo vigente.
        self.fields["image"].max_bytes = label_images.MAX_UPLOAD_BYTES

    def get_url(self, obj):
        request = self.context.get("request")
        url = obj.image.url
        return request.build_absolute_uri(url) if request is not None else url

    def validate_image(self, value):
        if value is None:
            raise serializers.ValidationError("Elegí una imagen.")
        if value.size > label_images.MAX_UPLOAD_BYTES:
            raise serializers.ValidationError(
                f"La imagen supera el tamaño máximo permitido ({label_images.MAX_UPLOAD_BYTES // (1024 * 1024)} MB)."
            )
        return label_images.normalize_upload(value)
