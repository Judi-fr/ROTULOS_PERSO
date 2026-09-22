from django.contrib import admin

from .models import LabelImport


@admin.register(LabelImport)
class LabelImportAdmin(admin.ModelAdmin):
    list_display = (
        "uploaded_file",
        "status",
        "model_name",
        "confidence",
        "input_tokens",
        "output_tokens",
        "created_by",
        "created_at",
    )
    list_filter = ("status", "model_name")
    search_fields = ("created_by__email", "uploaded_file__original_filename")
