"""Serializers de la administración de usuarios (CRUD del admin).

Reutiliza las mismas reglas de identidad y validación que la autenticación
(``views.py``): el email normalizado es el ``username`` del proyecto y la
contraseña pasa por la composición propia + los validators de Django.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password as django_validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .views import normalize_email, validate_email_format, validate_password_strength

User = get_user_model()


class UserAdminSerializer(serializers.ModelSerializer):
    """Representa un usuario para el CRUD del administrador.

    - ``password`` es de solo escritura: obligatorio al crear, opcional al
      editar (si viene, se rehashea; si no, la contraseña queda intacta).
    - ``email`` se normaliza y funciona como ``username`` (regla del proyecto),
      así que su unicidad se valida contra ``username``.
    """

    email = serializers.EmailField(required=True)
    password = serializers.CharField(
        write_only=True, required=False, style={"input_type": "password"}
    )
    # Los roles del proyecto son Groups (ver migración 0001_seed_roles). Se
    # exponen por NOMBRE para que el cliente mande p. ej. ["diseñadores"] en vez
    # de IDs. Si el nombre no existe, DRF responde 400 automáticamente.
    groups = serializers.SlugRelatedField(
        slug_field="name",
        queryset=Group.objects.all(),
        many=True,
        required=False,
    )

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
        ]
        read_only_fields = ["id", "date_joined", "last_login"]

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

    def _run_django_password_validators(self, password, user):
        try:
            django_validate_password(password, user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)})

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        groups = validated_data.pop("groups", None)
        if not password:
            raise serializers.ValidationError(
                {"password": "La contraseña es obligatoria al crear un usuario."}
            )

        email = validated_data["email"]
        first_name = validated_data.get("first_name", "")
        last_name = validated_data.get("last_name", "")

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

        # Los groups son M2M: se asignan después de crear el usuario.
        if groups is not None:
            user.groups.set(groups)
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        groups = validated_data.pop("groups", None)

        # El email es el username: si cambia, se actualizan ambos en conjunto.
        email = validated_data.get("email")
        if email:
            instance.email = email
            instance.username = email

        for field in ("first_name", "last_name", "is_active", "is_staff"):
            if field in validated_data:
                setattr(instance, field, validated_data[field])

        if password:
            self._run_django_password_validators(password, instance)
            instance.set_password(password)

        instance.save()

        # Solo se tocan los roles si vinieron en el body (M2M, se asigna aparte).
        # Mandar [] los borra; no mandar la clave los deja intactos.
        if groups is not None:
            instance.groups.set(groups)
        return instance


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
        ]
        read_only_fields = [
            "id",
            "email",
            "is_staff",
            "groups",
            "date_joined",
            "has_usable_password",
        ]

    def get_has_usable_password(self, obj):
        return obj.has_usable_password()
