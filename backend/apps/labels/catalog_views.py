"""``/api/v1/labels/sizes/``: el catálogo global de tamaños de rótulo
(``LabelSize``). Ver ``apps/common/catalogs.py``."""

from decimal import Decimal

from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from apps.common.catalogs import CatalogViewSet

from .models import LabelSize


class LabelSizeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabelSize
        fields = ["id", "name", "width_cm", "height_cm", "is_active"]
        read_only_fields = ["id"]
        # Los mismos límites que una plantilla (LabelTemplateSerializer).
        extra_kwargs = {
            "name": {"validators": [UniqueValidator(LabelSize.objects.all(), message="Ya hay un tamaño con ese nombre.")]},
            "width_cm": {"min_value": Decimal("5"), "max_value": Decimal("30")},
            "height_cm": {"min_value": Decimal("5"), "max_value": Decimal("40")},
        }

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Poné un nombre (p. ej. «Estándar 10 × 15»).")
        return value


class LabelSizeViewSet(CatalogViewSet):
    serializer_class = LabelSizeSerializer
    catalog_name = "Tamaño de rótulo"
