"""Serializers de documentos.

Todos son de solo lectura: un ``Document`` no se crea ni edita a mano
desde esta app — lo crea/actualiza el proceso que lo genera (hoy,
``apps.labels.batch_views``); acá solo se lista, se descarga o se
soft-borra (ver ``views.py``).
"""

from __future__ import annotations

from rest_framework import serializers

from .models import Document


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
