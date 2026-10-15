"""Contrato OpenAPI 3 de la API de ingesta (``/api/v1/ingest/``).

Es lo que se le pasa a quien integra su ERP o tienda propia: lo abre en
cualquier visor de OpenAPI (Swagger Editor, Postman, Insomnia) o genera un
cliente con él. Se sirve en ``GET /api/v1/ingest/openapi.json``, público: no
tiene datos, solo describe la API.

Se arma con código y no se escribe a mano: los campos de un pedido salen de
``apps.orders.ingestion.TARGET_FIELDS``, el mismo diccionario que valida cada
ítem, y el límite de pedidos por minuto de la configuración. Si se agrega un
campo allá, aparece acá solo. Lo que no se puede derivar (la forma de la
respuesta por ítem, la firma del webhook) está escrito una vez y tiene test.
"""

from __future__ import annotations

from django.conf import settings

from apps.orders.ingestion import TARGET_FIELDS

OPENAPI_VERSION = "3.0.3"
API_VERSION = "1.0.0"

# Ejemplo por campo, para que la documentación muestre un pedido real.
_EXAMPLES = {
    "destinatario": "María Gómez",
    "domicilio": "Av. Corrientes",
    "numero": "1234",
    "ciudad": "Ciudad Autónoma de Buenos Aires",
    "provincia": "Ciudad Autónoma de Buenos Aires",
    "cp": "C1043AAZ",
    "referencia": "Timbre 3B",
    "descripcion": "2 remeras",
    "external_id": "ERP-10045",
}

_DESCRIPTIONS = {
    "external_id": (
        "Identificador del pedido en tu sistema. Hace la llamada idempotente: si ya existe un "
        "pedido tuyo con ese external_id se devuelve el mismo, sin duplicarlo. Muy recomendado "
        "para poder reintentar sin miedo."
    ),
}


def _order_schema():
    properties = {}
    for key, meta in TARGET_FIELDS.items():
        prop = {"type": "string", "title": meta["label"]}
        if key in _DESCRIPTIONS:
            prop["description"] = _DESCRIPTIONS[key]
        if key in _EXAMPLES:
            prop["example"] = _EXAMPLES[key]
        properties[key] = prop
    return {
        "type": "object",
        "required": [key for key, meta in TARGET_FIELDS.items() if meta["required"]],
        "properties": properties,
        "additionalProperties": True,
        "description": "Un pedido. Las claves que no están en la lista se ignoran.",
    }


def _result_schema():
    return {
        "type": "object",
        "description": (
            "Resultado de UN ítem, en el mismo orden en que se mandaron. Un ítem con error no "
            "frena a los demás: revisá cada uno."
        ),
        "properties": {
            "id": {"type": "integer", "description": "Id del pedido en Rótulos (solo si salió bien)."},
            "external_id": {"type": "string", "nullable": True},
            "created": {
                "type": "boolean",
                "description": "true: se creó ahora. false: ya existía (mismo external_id) o hubo un error.",
            },
            "status": {"type": "string", "description": "Estado del pedido (created, preparing, dispatched...)."},
            "error": {"type": "string", "description": "Por qué no se cargó este ítem."},
        },
    }


def _results_response():
    return {
        "description": "Se procesó la llamada. El resultado de cada pedido va en `results`.",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {"results": {"type": "array", "items": {"$ref": "#/components/schemas/IngestResult"}}},
                },
                "example": {
                    "results": [
                        {"id": 812, "external_id": "ERP-10045", "created": True, "status": "created"},
                        {"external_id": "ERP-10046", "created": False, "error": "Falta 'Ciudad'."},
                    ]
                },
            }
        },
    }


def _error(description):
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
    }


def _orders_body():
    return {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "oneOf": [
                        {"$ref": "#/components/schemas/Order"},
                        {"type": "array", "minItems": 1, "items": {"$ref": "#/components/schemas/Order"}},
                    ]
                },
                "examples": {
                    "uno": {"summary": "Un pedido", "value": _EXAMPLES},
                    "varios": {
                        "summary": "Varios pedidos en una llamada",
                        "value": [_EXAMPLES, dict(_EXAMPLES, external_id="ERP-10046", destinatario="Juan Pérez")],
                    },
                },
            }
        },
    }


def build_ingest_openapi(server_url=""):
    """El documento OpenAPI como dict (se sirve como JSON)."""
    rate = settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {}).get("ingest", "")
    return {
        "openapi": OPENAPI_VERSION,
        "info": {
            "title": "Rótulos — API de ingesta de pedidos",
            "version": API_VERSION,
            "description": (
                "Mandá los pedidos de tu sistema (ERP, tienda propia) para que lleguen a Rótulos y se "
                "les pueda imprimir el rótulo y despachar.\n\n"
                "Dos formas de entrar, con el mismo formato de pedido:\n"
                "- `POST /orders/` con una clave de API (la genera el administrador en Integraciones).\n"
                "- `POST /webhooks/{slug}/` firmando el cuerpo con el secreto del webhook.\n\n"
                f"Límite: {rate or 'configurable'} llamadas por clave. Pasado el límite se responde 429; "
                "esperá y reintentá. Con `external_id` reintentar nunca duplica."
            ),
        },
        "servers": [{"url": server_url}] if server_url else [],
        "paths": {
            "/orders/": {
                "post": {
                    "summary": "Cargar uno o varios pedidos",
                    "operationId": "ingestOrders",
                    "security": [{"ApiKey": []}],
                    "requestBody": _orders_body(),
                    "responses": {
                        "200": _results_response(),
                        "400": _error("El cuerpo no es un pedido ni una lista, o la lista está vacía."),
                        "401": _error("Falta la clave de API, o es inválida o está inactiva."),
                        "429": _error("Demasiadas llamadas: esperá y reintentá."),
                    },
                }
            },
            "/webhooks/{slug}/": {
                "post": {
                    "summary": "Webhook entrante (firmado)",
                    "operationId": "ingestWebhook",
                    "description": (
                        "Para sistemas que solo saben mandar webhooks. Cada webhook tiene su propio "
                        "`slug`, su secreto y, si hace falta, un mapeo de campos (configurado en "
                        "Integraciones): con mapeo, las claves del cuerpo son las de tu sistema y se "
                        "traducen a las de un pedido.\n\n"
                        "La firma es el HMAC-SHA256 del cuerpo EXACTO que se manda (los bytes, antes de "
                        "cualquier reformateo), con el secreto del webhook, en hexadecimal, en el header "
                        "`X-Webhook-Signature`. Una firma inválida se rechaza sin crear nada."
                    ),
                    "parameters": [
                        {
                            "name": "slug",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Identificador del webhook.",
                        },
                        {
                            "name": "X-Webhook-Signature",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "hex(HMAC-SHA256(secreto, cuerpo)).",
                        },
                    ],
                    "security": [],
                    "requestBody": _orders_body(),
                    "responses": {
                        "200": _results_response(),
                        "400": _error("El cuerpo no es JSON válido o está vacío."),
                        "401": _error("Firma inválida."),
                        "404": _error("No existe un webhook activo con ese slug."),
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "ApiKey": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "Authorization",
                    "description": "`Authorization: Api-Key <tu clave>` (con la palabra Api-Key y un espacio).",
                }
            },
            "schemas": {
                "Order": _order_schema(),
                "IngestResult": _result_schema(),
                "Error": {
                    "type": "object",
                    "properties": {"detail": {"type": "string"}},
                    "example": {"detail": "Clave de API inválida o inactiva."},
                },
            },
        },
    }
