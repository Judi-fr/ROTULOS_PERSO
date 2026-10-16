"""``/api/v1/carriers/manual-carriers/``: el catálogo global de transportistas
sin API (``ManualCarrier``). Ver ``apps/common/catalogs.py``."""

from rest_framework import serializers

from apps.common.catalogs import CatalogViewSet

from .models import ManualCarrier
from .providers import all_providers


class ManualCarrierSerializer(serializers.ModelSerializer):
    class Meta:
        model = ManualCarrier
        fields = ["id", "name", "tracking_url_template", "is_active"]
        read_only_fields = ["id"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Poné el nombre del transportista.")
        taken = {provider.code for provider in all_providers()} | {provider.name.lower() for provider in all_providers()}
        if value.lower() in taken:
            raise serializers.ValidationError(
                f"«{value}» ya está como transportista con API: se elige desde Mis pedidos."
            )
        return value

    def validate_tracking_url_template(self, value):
        value = value.strip()
        if value and (not value.startswith(("https://", "http://")) or "{tracking}" not in value):
            raise serializers.ValidationError(
                "Tiene que ser una dirección web con {tracking} donde va el número, "
                "p. ej. https://ejemplo.com/seguimiento?numero={tracking}"
            )
        return value


class ManualCarrierViewSet(CatalogViewSet):
    serializer_class = ManualCarrierSerializer
    catalog_name = "Transportista manual"
