"""Serializers de la app processing."""

from rest_framework import serializers

from apps.documents.models import UploadedLabelFile

from .models import LabelImport


class LabelImportSerializer(serializers.ModelSerializer):
    """Una lectura de rótulo.

    Al crear solo se acepta ``uploaded_file``: todo lo demás lo produce el
    agente. El queryset se filtra por el usuario de la petición para que
    nadie pueda lanzar una importación —que cuesta tokens— sobre la foto de
    otra cuenta.
    """

    uploaded_file = serializers.PrimaryKeyRelatedField(queryset=UploadedLabelFile.objects.none())
    uploaded_file_name = serializers.CharField(
        source="uploaded_file.original_filename", read_only=True
    )
    confidence = serializers.FloatField(read_only=True)

    class Meta:
        model = LabelImport
        fields = [
            "id",
            "uploaded_file",
            "uploaded_file_name",
            "status",
            "model_name",
            "confidence",
            "proposal",
            "error",
            "input_tokens",
            "output_tokens",
            "request_id",
            "created_by",
            "created_at",
            "finished_at",
        ]
        read_only_fields = [
            "id",
            "uploaded_file_name",
            "status",
            "model_name",
            "confidence",
            "proposal",
            "error",
            "input_tokens",
            "output_tokens",
            "request_id",
            "created_by",
            "created_at",
            "finished_at",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None and request.user.is_authenticated:
            self.fields["uploaded_file"].queryset = UploadedLabelFile.objects.filter(
                uploaded_by=request.user
            )
