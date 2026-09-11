"""Serializers de la app processing."""

from rest_framework import serializers

from apps.documents.models import Documento

from .models import ImportacionRotulo


class ImportacionRotuloSerializer(serializers.ModelSerializer):
    """Una lectura de rótulo.

    Al crear solo se acepta ``documento``: todo lo demás lo produce el agente.
    El queryset se filtra por el usuario de la petición para que nadie pueda
    lanzar una importación —que cuesta tokens— sobre la foto de otra cuenta.
    """

    documento = serializers.PrimaryKeyRelatedField(queryset=Documento.objects.none())
    documento_nombre = serializers.CharField(
        source="documento.nombre_original", read_only=True
    )
    confianza = serializers.FloatField(read_only=True)

    class Meta:
        model = ImportacionRotulo
        fields = [
            "id",
            "documento",
            "documento_nombre",
            "estado",
            "modelo",
            "confianza",
            "propuesta",
            "error",
            "tokens_entrada",
            "tokens_salida",
            "request_id",
            "creada_por",
            "creada_en",
            "finalizada_en",
        ]
        read_only_fields = [
            "id",
            "documento_nombre",
            "estado",
            "modelo",
            "confianza",
            "propuesta",
            "error",
            "tokens_entrada",
            "tokens_salida",
            "request_id",
            "creada_por",
            "creada_en",
            "finalizada_en",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        peticion = self.context.get("request")
        if peticion is not None and peticion.user.is_authenticated:
            self.fields["documento"].queryset = Documento.objects.filter(
                subido_por=peticion.user
            )
