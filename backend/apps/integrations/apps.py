from django.apps import AppConfig

# Únicas rutas que aceptan CORS de cualquier origen: las de la extensión de
# impresión del admin de Shopify, que corre en un origen de Shopify. El
# enlace (print-link/) lo pide la extensión; el PDF (print/<token>) lo baja la
# vista previa de impresión, que también lo pide por fetch con preflight
# (visto en los logs). No abre nada más: las autentica el ID token de
# Shopify o el enlace firmado, no una cookie ni nuestro JWT.
SHOPIFY_PRINT_LINK_PATH = "/api/v1/integrations/shopify/print-link/"
SHOPIFY_PRINT_DOCUMENT_PREFIX = "/api/v1/integrations/shopify/print/"


def _allow_shopify_extension_cors(sender, request, **kwargs):
    return request.path == SHOPIFY_PRINT_LINK_PATH or request.path.startswith(SHOPIFY_PRINT_DOCUMENT_PREFIX)


class IntegrationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations"
    verbose_name = "Integraciones (API y webhooks)"

    def ready(self):
        # Registra los handlers de la cola de eventos de tiendas (ver
        # apps.integrations.handlers) en todo proceso: servidor, worker y tests.
        from . import handlers  # noqa: F401

        from corsheaders.signals import check_request_enabled

        check_request_enabled.connect(_allow_shopify_extension_cors, dispatch_uid="shopify-print-link-cors")
