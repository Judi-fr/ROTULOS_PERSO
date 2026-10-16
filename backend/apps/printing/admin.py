from django.contrib import admin

from .models import PrintAgent, Printer, PrintJob


@admin.register(PrintAgent)
class PrintAgentAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "token_prefix", "last_seen_at", "last_version", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "owner__email", "token_prefix")
    readonly_fields = ("token_prefix", "token_hash", "last_seen_at", "last_version", "created_at")


@admin.register(Printer)
class PrinterAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "format", "dpmm", "connection", "agent", "is_default", "is_active")
    list_filter = ("format", "connection", "is_active")
    search_fields = ("name", "owner__email", "location")


@admin.register(PrintJob)
class PrintJobAdmin(admin.ModelAdmin):
    list_display = ("pk", "owner", "printer", "status", "label_count", "attempts", "created_at", "finished_at")
    list_filter = ("status",)
    search_fields = ("owner__email", "printer__name", "error_message")
    readonly_fields = [field.name for field in PrintJob._meta.fields]
