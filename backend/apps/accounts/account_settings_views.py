"""``GET/PATCH /api/v1/auth/me/account/`` — los datos de la cuenta del
comerciante (``AccountSettings``, T#351-353): fiscales, el remitente y logo
por defecto de los rótulos y las preferencias de la app. Mismos permisos que
el perfil propio (``users.me.view`` / ``users.me.edit``)."""

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record
from apps.accounts.teams import data_owner, team_display_name, team_for

from .models import AccountSettings, cuit_is_valid
from .role_permissions import HasRolePermission

AUDITED_FIELDS = (
    "legal_name",
    "tax_id",
    "sender_name",
    "sender_address",
    "sender_phone",
    "default_template",
    "default_carrier",
    "default_print_format",
)


def _snapshot(instance):
    """Los campos auditados como texto (la plantilla, por su id)."""
    if instance is None:
        return {}
    return {
        field: str(getattr(instance, "default_template_id" if field == "default_template" else field) or "")
        for field in AUDITED_FIELDS
    }


class AccountSettingsSerializer(serializers.ModelSerializer):
    def __init__(self, *args, **kwargs):
        # Import tardío: apps.labels importa de apps.accounts.
        from apps.labels.serializers import ImageOrDataUrlField

        super().__init__(*args, **kwargs)
        self.fields["logo"] = ImageOrDataUrlField(
            required=False, allow_null=True, max_bytes=2 * 1024 * 1024, field_label="Logo"
        )

    class Meta:
        model = AccountSettings
        fields = [
            "legal_name",
            "tax_id",
            "sender_name",
            "sender_address",
            "sender_phone",
            "logo",
            "default_template",
            "default_carrier",
            "default_print_format",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]

    def validate_tax_id(self, value):
        value = value.strip()
        if not value:
            return ""
        if not cuit_is_valid(value):
            raise serializers.ValidationError("El CUIT no es válido: revisá los 11 números.")
        digits = "".join(char for char in value if char.isdigit())
        return f"{digits[:2]}-{digits[2:10]}-{digits[10]}"

    def validate_default_template(self, template):
        user = data_owner(self.context["request"].user)
        if template is not None and not (template.is_active and (template.is_public or template.owner_id == user.id)):
            raise serializers.ValidationError("Esa plantilla no existe o no es tuya.")
        return template

    def validate_default_carrier(self, value):
        """Un transportista con API (por su código) o uno del catálogo de
        transportistas manuales (por su nombre, como está escrito ahí)."""
        from apps.carriers.models import ManualCarrier
        from apps.carriers.providers import all_providers

        value = value.strip()
        if not value:
            return ""
        codes = {provider.code for provider in all_providers()}
        if value in codes:
            return value
        manual = ManualCarrier.objects.filter(name__iexact=value, is_active=True).first()
        if manual is None:
            raise serializers.ValidationError("Elegí un transportista de la lista.")
        return manual.name


class AccountSettingsView(APIView):
    def get_permissions(self):
        # Leerlos, cualquiera del comercio (los usan los valores por defecto
        # de varias pantallas); cambiarlos, el acceso "Datos del comercio".
        permission = "users.me.view" if self.request.method == "GET" else "account.manage"
        return [IsAuthenticated(), HasRolePermission(permission)]

    def _instance(self):
        return AccountSettings.objects.filter(user=data_owner(self.request.user)).first()

    def get(self, request):
        instance = self._instance() or AccountSettings(user=data_owner(request.user))
        return Response(AccountSettingsSerializer(instance, context={"request": request}).data)

    def patch(self, request):
        instance = self._instance()
        previous = _snapshot(instance)
        serializer = AccountSettingsSerializer(
            instance or AccountSettings(user=data_owner(request.user)), data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        saved = serializer.save(user=data_owner(request.user))
        current = _snapshot(saved)
        changes = {
            field: {"from": previous.get(field, ""), "to": value}
            for field, value in current.items()
            if previous.get(field, "") != value
        }
        if "logo" in serializer.validated_data:
            changes["logo"] = {"from": None, "to": saved.logo.name.rsplit("/", 1)[-1] if saved.logo else None}
        if changes:
            record(
                request,
                category="users",
                action="account.update",
                # Los datos son del comercio (no de una persona).
                target=team_for(request.user),
                target_type="team",
                target_repr=team_display_name(team_for(request.user)),
                changes=changes,
            )
        return Response(AccountSettingsSerializer(saved, context={"request": request}).data)
