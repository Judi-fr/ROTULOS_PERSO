"""Serializers de la app documents."""

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import Documento, validar_archivo


class DocumentoSerializer(serializers.ModelSerializer):
    """Un archivo subido.

    Todo lo que describe al archivo (``tipo_mime``, ``tamano_bytes``,
    ``nombre_original``) es de solo lectura y se deriva del contenido en
    ``validate_archivo``. Aceptarlos del cuerpo permitiría que el cliente
    declare un tipo que no se corresponde con lo que mandó.
    """

    archivo_url = serializers.SerializerMethodField()

    class Meta:
        model = Documento
        fields = [
            "id",
            "archivo",
            "archivo_url",
            "nombre_original",
            "tipo_mime",
            "tamano_bytes",
            "subido_por",
            "subido_en",
        ]
        read_only_fields = [
            "id",
            "archivo_url",
            "nombre_original",
            "tipo_mime",
            "tamano_bytes",
            "subido_por",
            "subido_en",
        ]

    def get_archivo_url(self, obj):
        """URL absoluta del archivo, para que el frontend pueda previsualizarlo."""
        if not obj.archivo:
            return None
        peticion = self.context.get("request")
        url = obj.archivo.url
        return peticion.build_absolute_uri(url) if peticion else url

    def validate_archivo(self, archivo):
        try:
            validar_archivo(archivo)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return archivo

    def create(self, validated_data):
        # El tipo se vuelve a detectar del contenido (no se confía en la
        # petición) y el resto de los metadatos salen del archivo en sí.
        archivo = validated_data["archivo"]
        validated_data["tipo_mime"] = validar_archivo(archivo)
        validated_data["nombre_original"] = archivo.name[:255]
        validated_data["tamano_bytes"] = archivo.size
        return super().create(validated_data)
