"""Serializers de documentos.

Todos son de solo lectura: un ``Document`` no se crea ni edita a mano
desde esta app — lo crea/actualiza el proceso que lo genera (hoy,
``apps.labels.batch_views``); acá solo se lista, se descarga o se
soft-borra (ver ``views.py``).
"""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import Document, Documento, validar_archivo


class DocumentSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    file_name = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            "id",
            "name",
            "kind",
            "kind_label",
            "status",
            "status_label",
            "file_name",
            "item_count",
            "size_bytes",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_file_name(self, obj):
        if not obj.file:
            return None
        return obj.file.name.rsplit("/", 1)[-1]


class AdminDocumentSerializer(DocumentSerializer):
    """Lectura de documentos de TODOS los usuarios (panel admin,
    ``documents.view_all``). Mismo criterio que ``AdminLabelSerializer``."""

    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta(DocumentSerializer.Meta):
        fields = ["id", "user", "user_email"] + [
            f for f in DocumentSerializer.Meta.fields if f != "id"
        ]
        read_only_fields = fields

class DocumentoSerializer(serializers.ModelSerializer):
    """Un archivo fuente subido (para importar), distinto de ``Document``.

    Todo lo que describe al archivo (``tipo_mime``, ``tamano_bytes``,
    ``nombre_original``) es de solo lectura y se deriva del contenido en
    ``validate_archivo``: aceptarlos del cuerpo permitiría que el cliente
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
        archivo = validated_data["archivo"]
        validated_data["tipo_mime"] = validar_archivo(archivo)
        validated_data["nombre_original"] = archivo.name[:255]
        validated_data["tamano_bytes"] = archivo.size
        return super().create(validated_data)
