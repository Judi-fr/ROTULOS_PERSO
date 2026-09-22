from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations"
    verbose_name = "Integraciones (API y webhooks)"

    def ready(self):
        # Registra los handlers de la cola de eventos de tiendas (ver
        # apps.integrations.handlers) en todo proceso: servidor, worker y tests.
        from . import handlers  # noqa: F401
