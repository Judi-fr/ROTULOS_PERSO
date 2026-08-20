"""Serializers de la administración de usuarios (CRUD del admin).

Reutiliza las mismas reglas de identidad y validación que la autenticación
(``views.py``): el email normalizado es el ``username`` del proyecto y la
contraseña pasa por la composición propia + los validators de Django.

Además de mantener el payload clásico (``email``, ``first_name``/``last_name``,
``groups``, ``is_active``), este serializer:

- acepta el payload del panel administrativo (``full_name``, ``role``, ``status``);
- expone campos calculados de salida que consume el frontend
  (``display_name``, ``role_key``, ``role_label``, ``status_key``,
  ``status_label``, ``created_date``, ``can_reactivate``);
- define los roles del sistema y sus Groups, usados también por
  ``viewsets.py`` (filtros por rol) y ``permissions_map.py``.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password as django_validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .views import normalize_email, validate_email_format, validate_password_strength

User = get_user_model()

# Roles válidos del sistema (coincide con permissions_map.VALID_ROLES).
ROLE_CHOICES = ("admin", "designer", "operator", "subscriber")

# Alias (legacy y variantes en español) -> rol canónico.
ROLE_ALIASES = {
    "administrador": "admin",
    "administrator": "admin",
    "diseñador": "designer",
    "disenador": "designer",
    "operador": "operator",
}

# Rol canónico -> nombre de Group. El group canónico "admin" es distinto del
# group "administradores" sembrado por 0001_seed_roles; asegurar ambos no borra
# nada, solo garantiza que el filtro por rol de viewsets.py encuentre el group.
ROLE_GROUP_MAP = {
    "admin": "admin",
    "designer": "designer",
    "operator": "operator",
    "subscriber": "subscriber",
}


def normalize_role_name(name):
    """Devuelve el rol canónico a partir de un nombre de rol/group (o el input tal cual)."""
    key = (name or "").strip().lower()
    return ROLE_ALIASES.get(key, key)


def ensure_role_groups():
    """Crea los Groups de rol del sistema si no existen (idempotente)."""
    names = set(ROLE_GROUP_MAP.values())
    for name in names:
        Group.objects.get_or_create(name=name)
    return list(Group.objects.filter(name__in=names))


def get_user_role(user):
    """Rol efectivo del usuario (misma lógica que permissions_map.get_effective_role).

    1. Group cuyo nombre normaliza a un rol válido.
    2. Fallback: ``is_staff`` -> ``admin``.
    3. Fallback final: ``subscriber``.
    """
    if user is None:
        return "subscriber"
    names = {normalize_role_name(g.name) for g in user.groups.all()}
    for role in ROLE_CHOICES:
        if role in names:
            return role
    return "admin" if getattr(user, "is_staff", False) else "subscriber"


class UserAdminSerializer(serializers.ModelSerializer):
    """Representa un usuario para el CRUD del administrador.

    - ``password`` es de solo escritura: obligatorio al crear, opcional al
      editar (si viene, se rehashea; si no, la contraseña queda intacta).
    - ``email`` se normaliza y funciona como ``username`` (regla del proyecto).
    - ``full_name`` / ``role`` / ``status`` son alias de entrada del panel
      administrativo; el payload clásico (``first_name`` / ``last_name`` /
      ``groups`` / ``is_active``) también sigue siendo aceptado.
    - Los campos ``display_name`` / ``role_*`` / ``status_*`` / ``created_date``
      / ``can_reactivate`` son de salida y los consume ``admingestion_test.js``.
    """

    email = serializers.EmailField(required=True)
    password = serializers.CharField(
        write_only=True, required=False, style={"input_type": "password"}
    )
    # Los roles del proyecto se exponen por NOMBRE de Group (payload clásico).
    groups = serializers.SlugRelatedField(
        slug_field="name",
        queryset=Group.objects.all(),
        many=True,
        required=False,
    )

    # Alias de entrada del panel administrativo del frontend.
    full_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    role = serializers.CharField(write_only=True, required=False, allow_blank=True)
    status = serializers.ChoiceField(
        write_only=True,
        required=False,
        choices=[("active", "active"), ("inactive", "inactive")],
    )

    # Campos calculados de salida (frontend).
    display_name = serializers.SerializerMethodField()
    role_key = serializers.SerializerMethodField()
    role_label = serializers.SerializerMethodField()
    status_key = serializers.SerializerMethodField()
    status_label = serializers.SerializerMethodField()
    created_date = serializers.SerializerMethodField()
    can_reactivate = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "password",
            "first_name",
            "last_name",
            "is_active",
            "is_staff",
            "groups",
            "date_joined",
            "last_login",
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
        ]
        read_only_fields = [
            "id",
            "date_joined",
            "last_login",
            "display_name",
            "role_key",
            "role_label",
            "status_key",
            "status_label",
            "created_date",
            "can_reactivate",
        ]

    # --- Campos calculados de salida --------------------------------------

    def get_display_name(self, obj):
        full = " ".join(filter(None, [obj.first_name, obj.last_name])).strip()
        return full or obj.email or f"Usuario {obj.pk}"

    def get_role_key(self, obj):
        return get_user_role(obj)

    def get_role_label(self, obj):
        return get_user_role(obj).capitalize()

    def get_status_key(self, obj):
        return "active" if obj.is_active else "inactive"

    def get_status_label(self, obj):
        return "Active" if obj.is_active else "Inactive"

    def get_created_date(self, obj):
        if not obj.date_joined:
            return None
        return obj.date_joined.strftime("%Y-%m-%d")

    def get_can_reactivate(self, obj):
        return not obj.is_active

    # --- Helpers -----------------------------------------------------------

    @staticmethod
    def _split_full_name(full_name):
        parts = (full_name or "").strip().split()
        if not parts:
            return "", ""
        return parts[0], " ".join(parts[1:])

    @classmethod
    def _role_to_group_names(cls, role):
        if not role:
            return None
        canonical = normalize_role_name(role)
        mapped = ROLE_GROUP_MAP.get(canonical)
        return [mapped] if mapped else []

    @classmethod
    def _status_to_is_active(cls, status):
        if status == "inactive":
            return False
        if status == "active":
            return True
        return None

    # --- Validaciones ------------------------------------------------------

    def validate_email(self, value):
        """Normaliza, valida el formato y garantiza unicidad (excluyendo al
        propio usuario en las ediciones)."""
        email = normalize_email(value)
        error = validate_email_format(email)
        if error:
            raise serializers.ValidationError(error)

        qs = User.objects.filter(username__iexact=email)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Ya existe una cuenta con ese email.")
        return email

    def validate_password(self, value):
        """Regla de composición del proyecto (mínimo 6, mayúscula y número).

        Los validators de Django (que necesitan el objeto usuario para
        comparar similitud con el email) corren aparte en create/update.
        """
        error = validate_password_strength(value)
        if error:
            raise serializers.ValidationError(error)
        return value

    def validate_role(self, value):
        """Valida que el rol (alias del panel) sea uno de los conocidos."""
        if value is None or str(value).strip() == "":
            return value
        canonical = normalize_role_name(value)
        if canonical not in ROLE_CHOICES:
            raise serializers.ValidationError(f"Rol inválido: {value!r}.")
        return canonical

    def _run_django_password_validators(self, password, user):
        try:
            django_validate_password(password, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})

    # --- Create / Update ---------------------------------------------------

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        groups = validated_data.pop("groups", None)
        role = validated_data.pop("role", None)
        status = validated_data.pop("status", None)
        full_name = validated_data.pop("full_name", None)

        if not password:
            raise serializers.ValidationError(
                {"password": "La contraseña es obligatoria al crear un usuario."}
            )

        email = validated_data["email"]

        # full_name (panel) -> first_name/last_name, salvo que el payload clásico
        # traiga first_name/last_name explícitos.
        if full_name:
            first_name, last_name = self._split_full_name(full_name)
            validated_data.setdefault("first_name", first_name)
            validated_data.setdefault("last_name", last_name)
        first_name = validated_data.get("first_name", "")
        last_name = validated_data.get("last_name", "")

        # status (panel) -> is_active.
        is_active = self._status_to_is_active(status)
        if is_active is not None:
            validated_data["is_active"] = is_active

        # role admin (panel) -> user staff para que pueda usar el CRUD.
        if role == "admin":
            validated_data["is_staff"] = True

        # Usuario temporal (sin persistir) para que los validators de Django
        # puedan comparar la contraseña contra el email/nombre.
        self._run_django_password_validators(
            password,
            User(username=email, email=email, first_name=first_name, last_name=last_name),
        )

        # create_user hashea la contraseña. is_active/is_staff van como
        # extra_fields (por defecto True/False respectivamente).
        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            is_active=validated_data.get("is_active", True),
            is_staff=validated_data.get("is_staff", False),
        )

        # Groups: prioridad al payload clásico (groups), si viene; si no, se
        # deriva del rol del panel.
        target_groups = groups
        if target_groups is None and role:
            role_group_name = self._role_to_group_names(role)
            if role_group_name:
                self._ensure_groups_exist(role_group_name)
                target_groups = list(Group.objects.filter(name__in=role_group_name))

        if target_groups is not None:
            user.groups.set(target_groups)
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        groups = validated_data.pop("groups", None)
        role = validated_data.pop("role", None)
        status = validated_data.pop("status", None)
        full_name = validated_data.pop("full_name", None)

        # El email es el username: si cambia, se actualizan ambos en conjunto.
        email = validated_data.get("email")
        if email:
            instance.email = email
            instance.username = email

        # full_name (panel) -> first_name/last_name.
        if full_name:
            first_name, last_name = self._split_full_name(full_name)
            validated_data.setdefault("first_name", first_name)
            validated_data.setdefault("last_name", last_name)

        # status (panel) -> is_active (activar/desactivar / reactivar).
        is_active = self._status_to_is_active(status)
        if is_active is not None:
            validated_data["is_active"] = is_active

        # role admin -> staff para que conserve acceso al CRUD.
        if role == "admin":
            validated_data["is_staff"] = True

        for field in ("first_name", "last_name", "is_active", "is_staff"):
            if field in validated_data:
                setattr(instance, field, validated_data[field])

        if password:
            self._run_django_password_validators(password, instance)
            instance.set_password(password)

        instance.save()

        # Groups: prioridad al payload clásico si viene; si no, se deriva del rol.
        target_groups = groups
        if target_groups is None and role:
            role_group_name = self._role_to_group_names(role)
            if role_group_name:
                self._ensure_groups_exist(role_group_name)
                target_groups = list(Group.objects.filter(name__in=role_group_name))

        if target_groups is not None:
            instance.groups.set(target_groups)
        return instance

    @staticmethod
    def _ensure_groups_exist(names):
        for name in names:
            Group.objects.get_or_create(name=name)


class ChangePasswordSerializer(serializers.Serializer):
    """Cambio de contraseña del usuario autenticado.

    Exige la contraseña actual antes de permitir el cambio (se verifica en la
    vista contra ``request.user.check_password``) y que la nueva se confirme.
    """

    current_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(required=True, write_only=True, min_length=8)
    confirm_password = serializers.CharField(required=True, write_only=True)

    def validate(self, data):
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError(
                {"confirm_password": "Las contraseñas nuevas no coinciden."}
            )
        return data


class ProfileSerializer(serializers.ModelSerializer):
    """Perfil del usuario autenticado (self-service).

    A diferencia de ``UserAdminSerializer`` (CRUD del admin), acá el usuario
    opera sobre su PROPIA cuenta, así que solo ``first_name`` y ``last_name``
    son editables:

    - ``email`` es la identidad (username) del proyecto y no se cambia por
      esta vía (queda para el admin).
    - ``is_staff`` y ``groups`` son de solo lectura para que un usuario no
      pueda auto-promoverse.
    - ``has_usable_password`` le dice al frontend si la cuenta tiene una
      contraseña propia (login manual) o entró solo por Google (sin password).
    """

    groups = serializers.SlugRelatedField(slug_field="name", many=True, read_only=True)
    has_usable_password = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "is_staff",
            "groups",
            "date_joined",
            "has_usable_password",
            "display_name",
        ]
        read_only_fields = [
            "id",
            "email",
            "is_staff",
            "groups",
            "date_joined",
            "has_usable_password",
            "display_name",
        ]

    def get_has_usable_password(self, obj):
        return obj.has_usable_password()

    def get_display_name(self, obj):
        full = " ".join(filter(None, [obj.first_name, obj.last_name])).strip()
        return full or obj.email or f"Usuario {obj.pk}"
