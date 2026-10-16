from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from apps.labels.zpl import DEFAULT_DPMM, SUPPORTED_DPMM

from .models import PrintAgent, Printer, PrintJob


class PrintAgentSerializer(serializers.ModelSerializer):
    # "En línea" = pidió trabajos hace poco (el agente consulta cada pocos
    # segundos). Es lo que el comerciante necesita saber: si lo que mande va
    # a salir.
    is_online = serializers.SerializerMethodField()

    class Meta:
        model = PrintAgent
        fields = ["id", "name", "token_prefix", "last_seen_at", "last_version", "is_online", "created_at"]
        read_only_fields = ["id", "token_prefix", "last_seen_at", "last_version", "is_online", "created_at"]

    def get_is_online(self, obj):
        if not obj.last_seen_at:
            return False
        window = timedelta(seconds=settings.PRINT_AGENT_ONLINE_SECONDS)
        return timezone.now() - obj.last_seen_at <= window

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Poné un nombre para reconocer la PC (p. ej. «PC del depósito»).")
        return value


class PrinterSerializer(serializers.ModelSerializer):
    agent_name = serializers.CharField(source="agent.name", read_only=True, default=None)

    class Meta:
        model = Printer
        fields = [
            "id",
            "name",
            "format",
            "dpmm",
            "location",
            "connection",
            "host",
            "port",
            "queue",
            "agent",
            "agent_name",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "agent_name", "created_at", "updated_at"]

    def validate_agent(self, agent):
        user = self.context["request"].user
        if agent is not None and (agent.owner_id != user.id or not agent.is_active):
            raise serializers.ValidationError("Ese agente no existe.")
        return agent

    def validate(self, attrs):
        current = self.instance
        get = lambda key, default=None: attrs.get(key, getattr(current, key, default) if current else default)  # noqa: E731

        if not str(get("name", "") or "").strip():
            raise serializers.ValidationError({"name": "Poné un nombre para la impresora."})
        if get("format", Printer.Format.PDF) == Printer.Format.ZPL:
            dpmm = get("dpmm") or DEFAULT_DPMM
            if dpmm not in SUPPORTED_DPMM:
                raise serializers.ValidationError({"dpmm": "Densidad no soportada (8 = 203 dpi, 12 = 300 dpi)."})
            attrs["dpmm"] = dpmm
        else:
            attrs["dpmm"] = None

        if get("connection", Printer.Connection.NETWORK) == Printer.Connection.NETWORK:
            if not str(get("host", "") or "").strip():
                raise serializers.ValidationError({"host": "Falta la IP (o el nombre) de la impresora en tu red."})
            port = get("port", 9100) or 9100
            if not 1 <= int(port) <= 65535:
                raise serializers.ValidationError({"port": "El puerto va de 1 a 65535 (las térmicas usan 9100)."})
        elif not str(get("queue", "") or "").strip():
            raise serializers.ValidationError({"queue": "Falta el nombre de la impresora en la PC del agente."})
        return attrs


class PrintJobSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    printer_name = serializers.CharField(source="printer.name", read_only=True, default=None)
    document_name = serializers.CharField(source="document.name", read_only=True, default=None)

    class Meta:
        model = PrintJob
        fields = [
            "id",
            "status",
            "status_display",
            "printer",
            "printer_name",
            "document",
            "document_name",
            "label_count",
            "attempts",
            "error_message",
            "created_at",
            "claimed_at",
            "finished_at",
        ]
        read_only_fields = fields
