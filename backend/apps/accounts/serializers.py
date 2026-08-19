from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.conf import settings
from rest_framework import serializers

User = get_user_model()

ROLE_CHOICES = ("admin", "designer", "operator", "subscriber")
ROLE_LABELS = {
    "admin": "Admin",
    "designer": "Designer",
    "operator": "Operator",
    "subscriber": "Subscriber",
}
DEFAULT_ROLE = "subscriber"


def ensure_role_groups():
    """Crea los Groups de rol si aún no existen."""
    for name in ROLE_CHOICES:
        Group.objects.get_or_create(name=name)


def get_user_role(user):
    """Obtiene el rol del usuario basándose en su Group de Django."""
    user_groups = set(user.groups.values_list("name", flat=True))
    for role in ROLE_CHOICES:
        if role in user_groups:
            return role
    # Compatibilidad: staff sin grupo se interpreta como admin.
    if getattr(user, "is_staff", False):
        return "admin"
    return DEFAULT_ROLE


def set_user_role(user, role):
    """Asigna exactamente un rol (Group) al usuario y sincroniza is_staff."""
    if role not in ROLE_CHOICES:
        role = DEFAULT_ROLE

    ensure_role_groups()
    group = Group.objects.get(name=role)
    # Un solo rol a la vez: reemplaza cualquier Group previo.
    user.groups.set([group])

    should_be_staff = role == "admin"
    if user.is_staff != should_be_staff:
        user.is_staff = should_be_staff
        user.save(update_fields=["is_staff"])


class UserAdminSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, allow_blank=False)
    full_name = serializers.CharField(write_only=True, required=False, allow_blank=False)
    role = serializers.ChoiceField(
        choices=ROLE_CHOICES + ("user",), write_only=True, required=False
    )
    status = serializers.ChoiceField(
        choices=("active", "inactive", "locked"), write_only=True, required=False
    )
    display_name = serializers.SerializerMethodField(read_only=True)
    role_key = serializers.SerializerMethodField(read_only=True)
    role_label = serializers.SerializerMethodField(read_only=True)
    status_key = serializers.SerializerMethodField(read_only=True)
    status_label = serializers.SerializerMethodField(read_only=True)
    created_date = serializers.SerializerMethodField(read_only=True)
    can_reactivate = serializers.SerializerMethodField(read_only=True)
    can_deactivate = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "is_active",
            "is_staff",
            "date_joined",
            "password",
            "full_name",
            "role",
            "status",
            "display_name",
            "role_key",
            "role_label",
            "status_key",
            "status_label",
            "created_date",
            "can_reactivate",
            "can_deactivate",
        ]
        read_only_fields = ["first_name", "last_name", "is_active", "is_staff", "date_joined"]

    def get_display_name(self, user):
        return " ".join(part for part in (user.first_name, user.last_name) if part).strip() or "Usuario"

    def get_role_key(self, user):
        return get_user_role(user)

    def get_role_label(self, user):
        return ROLE_LABELS[get_user_role(user)]

    def get_status_key(self, user):
        return "active" if user.is_active else "inactive"

    def get_status_label(self, user):
        return "Active" if user.is_active else "Inactive"

    def get_created_date(self, user):
        return user.date_joined.strftime("%d/%m/%Y") if user.date_joined else "-"

    def get_can_reactivate(self, user):
        return not user.is_active

    def get_can_deactivate(self, user):
        return user.is_active

    def validate_email(self, value):
        return value.strip().lower()

    def validate(self, attrs):
        full_name = attrs.pop("full_name", None)
        role = attrs.pop("role", None)
        status = attrs.pop("status", None)
        password = attrs.pop("password", None)

        if self.instance is None and not full_name:
            raise serializers.ValidationError({"full_name": "El nombre completo es obligatorio."})
        if self.instance is None and status is None:
            raise serializers.ValidationError({"status": "El estado es obligatorio."})

        # La contraseña es opcional al crear: si no se envía,
        # create() usa settings.ADMIN_CREATED_USER_PASSWORD.
        # Si se envía, debe ser no vacía (allow_blank=False valida esto).
        if password is not None:
            attrs["password"] = password

        if full_name is not None:
            names = full_name.strip().split(maxsplit=1)
            attrs["first_name"] = names[0]
            attrs["last_name"] = names[1] if len(names) > 1 else ""

        # Rol predeterminado para usuarios nuevos: subscriber.
        if role is None and self.instance is None:
            role = DEFAULT_ROLE

        # "user" es un alias legacy de subscriber (compatibilidad con el frontend).
        if role == "user":
            role = DEFAULT_ROLE

        if role is not None:
            attrs["role"] = role
            attrs["is_staff"] = role == "admin"

        if status is not None:
            attrs["is_active"] = status == "active"

        email = attrs.get("email")
        if email:
            existing_user = User.objects.filter(username__iexact=email)
            if self.instance:
                existing_user = existing_user.exclude(pk=self.instance.pk)
            if existing_user.exists():
                raise serializers.ValidationError({"email": "Ya existe una cuenta con ese email."})

        return attrs

    def create(self, validated_data):
        role = validated_data.pop("role", DEFAULT_ROLE)
        password = validated_data.pop("password", settings.ADMIN_CREATED_USER_PASSWORD)
        email = validated_data["email"]
        validated_data["is_staff"] = role == "admin"
        user = User.objects.create_user(username=email, password=password, **validated_data)
        set_user_role(user, role)
        return user

    def update(self, instance, validated_data):
        role = validated_data.pop("role", None)
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        if "email" in validated_data:
            instance.username = instance.email
        instance.save()
        if role is not None:
            set_user_role(instance, role)
        return instance


class UserProfileSerializer(serializers.ModelSerializer):
    """Perfil propio: consulta y edición limitada del usuario autenticado."""

    full_name = serializers.CharField(write_only=True, required=False, allow_blank=False)
    current_password = serializers.CharField(write_only=True, required=False, allow_blank=False)
    new_password = serializers.CharField(write_only=True, required=False, allow_blank=False)
    confirm_new_password = serializers.CharField(write_only=True, required=False, allow_blank=False)
    display_name = serializers.SerializerMethodField(read_only=True)
    role_key = serializers.SerializerMethodField(read_only=True)
    role_label = serializers.SerializerMethodField(read_only=True)
    status_key = serializers.SerializerMethodField(read_only=True)
    status_label = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "display_name",
            "role_key",
            "role_label",
            "status_key",
            "status_label",
            "date_joined",
            "current_password",
            "new_password",
            "confirm_new_password",
        ]
        read_only_fields = ["id", "date_joined"]

    def get_display_name(self, user):
        return " ".join(part for part in (user.first_name, user.last_name) if part).strip() or "Usuario"

    def get_role_key(self, user):
        return get_user_role(user)

    def get_role_label(self, user):
        return ROLE_LABELS[get_user_role(user)]

    def get_status_key(self, user):
        return "active" if user.is_active else "inactive"

    def get_status_label(self, user):
        return "Active" if user.is_active else "Inactive"

    def validate_email(self, value):
        return value.strip().lower()

    def validate(self, attrs):
        # Campos administrativos: se ignoran si llegan por el body.
        for forbidden in (
            "role",
            "role_key",
            "role_label",
            "is_staff",
            "status",
            "status_key",
            "status_label",
            "is_active",
            "groups",
            "user_permissions",
        ):
            attrs.pop(forbidden, None)

        full_name = attrs.pop("full_name", None)
        if full_name is not None:
            names = full_name.strip().split(maxsplit=1)
            attrs["first_name"] = names[0]
            attrs["last_name"] = names[1] if len(names) > 1 else ""

        current_password = attrs.pop("current_password", None)
        new_password = attrs.pop("new_password", None)
        confirm_new_password = attrs.pop("confirm_new_password", None)

        password_fields_provided = any(
            value is not None for value in (current_password, new_password, confirm_new_password)
        )
        if password_fields_provided:
            missing = []
            if not current_password:
                missing.append("current_password")
            if not new_password:
                missing.append("new_password")
            if not confirm_new_password:
                missing.append("confirm_new_password")
            if missing:
                raise serializers.ValidationError(
                    {field: "Este campo es obligatorio para cambiar la contraseña." for field in missing}
                )
            if new_password != confirm_new_password:
                raise serializers.ValidationError(
                    {"confirm_new_password": "La nueva contraseña y su confirmación no coinciden."}
                )
            user = self.instance
            if user is None or not user.check_password(current_password):
                raise serializers.ValidationError(
                    {"current_password": "La contraseña actual es incorrecta."}
                )
            attrs["new_password"] = new_password

        email = attrs.get("email")
        if email:
            existing_user = User.objects.filter(username__iexact=email)
            if self.instance:
                existing_user = existing_user.exclude(pk=self.instance.pk)
            if existing_user.exists():
                raise serializers.ValidationError({"email": "Ya existe una cuenta con ese email."})

        return attrs

    def update(self, instance, validated_data):
        new_password = validated_data.pop("new_password", None)

        # Nunca permitir cambios de rol/staff/estado desde el perfil propio.
        validated_data.pop("is_staff", None)
        validated_data.pop("is_active", None)
        validated_data.pop("role", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if "email" in validated_data:
            instance.username = instance.email
        if new_password:
            instance.set_password(new_password)
        instance.save()
        return instance

