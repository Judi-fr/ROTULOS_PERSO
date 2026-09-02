from rest_framework import serializers

from .models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    category_label = serializers.CharField(source="get_category_display", read_only=True)
    action_label = serializers.CharField(source="get_action_display", read_only=True)
    actor_id = serializers.IntegerField(source="actor.id", read_only=True, default=None)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "actor_id",
            "actor_email",
            "category",
            "category_label",
            "action",
            "action_label",
            "target_type",
            "target_id",
            "target_repr",
            "changes",
            "ip_address",
            "user_agent",
            "created_at",
        ]
        read_only_fields = fields
