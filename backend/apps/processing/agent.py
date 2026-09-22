"""Agente que lee un rótulo desde una foto y propone una plantilla.

Flujo completo:

1. Se arma el esquema y las instrucciones a partir del catálogo activo
   (``schema.py``) — así una variable dada de alta hoy ya se puede detectar.
2. Se le manda la imagen (o el PDF) a Claude con *structured outputs*, que
   obliga a la respuesta a validar contra ese esquema. No hace falta parsear
   texto libre ni rezar para que el JSON venga bien formado.
3. Se convierte el resultado —que viene en porcentajes— al formato exacto que
   espera ``ElementLayoutSerializer``, en milímetros.

El paso 3 no es un detalle: la conversión también **descarta** los elementos
que violarían las reglas del modelo de datos (un texto estático sin texto, una
variable sin variable). Es preferible entregar una propuesta con un elemento
de menos, que el usuario agrega a mano, que una que el POST a
``/element-layouts/`` va a rechazar entera.
"""

import base64
import json
import logging

import anthropic
from django.conf import settings
from django.utils import timezone

from apps.labels.styles import DEFAULT_STYLE
from apps.labels.models import LayoutVariable

from .schema import (
    DEFAULT_HEIGHT_MM,
    DEFAULT_WIDTH_MM,
    build_schema,
    build_system_prompt,
)
from .models import LabelImportStatus

logger = logging.getLogger(__name__)

# Capas de dibujado: las líneas y los recuadros van al fondo para que el texto
# quede encima. Coincide con la semántica de `LayoutElement.order`.
BACKGROUND_ORDER = 5
CONTENT_ORDER = 20

# Grosor de trazo por defecto para líneas y recuadros. No se le pide al modelo
# porque no es algo que se pueda estimar de una foto con ninguna precisión.
DEFAULT_STROKE_WIDTH_MM = DEFAULT_STYLE["grosor_mm"]

# Tamaños mínimos que exige `LayoutElement` (MinValueValidator).
MIN_MM = 0.01


class AgentError(Exception):
    """Falla al interpretar un rótulo. El mensaje es apto para mostrar al usuario."""


def _client():
    """Devuelve el cliente de Anthropic, o falla con un mensaje claro."""
    if not settings.ANTHROPIC_API_KEY:
        raise AgentError(
            "Falta configurar ANTHROPIC_API_KEY. Agregala al archivo .env "
            "del backend y reiniciá el servidor."
        )
    return anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        # La lectura de un rótulo tarda entre 10 y 60 segundos. El default del
        # SDK son 10 minutos, más que suficiente, pero se acota para que una
        # petición colgada no deje esperando al usuario indefinidamente.
        timeout=settings.ANTHROPIC_TIMEOUT,
    )


def _document_block(document):
    """Arma el bloque de contenido con el archivo, según sea imagen o PDF."""
    with document.file.open("rb") as f:
        data = base64.standard_b64encode(f.read()).decode("utf-8")

    if document.is_image:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": document.mime_type,
                "data": data,
            },
        }
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": data,
        },
    }


def process(label_import):
    """Procesa una importación de punta a punta y la deja guardada.

    Muta y guarda ``label_import``: al terminar queda en estado ``completada``
    (con ``proposal``) o ``error`` (con ``error``). No lanza excepción por una
    falla del modelo — el error queda registrado en la fila, que es lo que la
    vista devuelve.
    """
    label_import.status = LabelImportStatus.PROCESSING
    label_import.model_name = settings.ANTHROPIC_MODEL
    label_import.save(update_fields=["status", "model_name"])

    try:
        response, usage, request_id = _request_reading(label_import.uploaded_file)
    except AgentError as exc:
        return _mark_error(label_import, str(exc))
    except anthropic.APIError as exc:
        # El detalle técnico va al log; al usuario se le dice qué puede hacer.
        logger.exception("Falló la lectura del rótulo %s", label_import.pk)
        return _mark_error(label_import, _error_message(exc))
    except ValueError as exc:
        # JSONDecodeError es un ValueError. Con structured outputs no debería
        # pasar, pero la vista promete no lanzar: si escapara, sería un 500 en
        # lugar de una importación en estado `error`.
        logger.exception("JSON inválido en la importación %s", label_import.pk)
        return _mark_error(
            label_import, f"El modelo devolvió una respuesta ilegible: {exc}"
        )

    label_import.raw_response = response
    label_import.input_tokens = usage.get("input", 0)
    label_import.output_tokens = usage.get("output", 0)
    label_import.request_id = request_id or ""

    try:
        label_import.proposal = build_proposal(response, label_import.uploaded_file)
    except (KeyError, TypeError, ValueError) as exc:
        logger.exception("Respuesta inesperada en la importación %s", label_import.pk)
        return _mark_error(
            label_import,
            "El modelo devolvió una respuesta que no se pudo interpretar. "
            f"Detalle: {exc}",
        )

    label_import.status = LabelImportStatus.COMPLETED
    label_import.finished_at = timezone.now()
    label_import.save()
    return label_import


def _request_reading(document):
    """Llama al modelo. Devuelve ``(response_dict, usage, request_id)``."""
    variables = list(LayoutVariable.objects.filter(is_active=True))
    if not variables:
        raise AgentError(
            "El catálogo de variables está vacío: no hay campos que detectar. "
            "Aplicá las migraciones o dá de alta variables antes de importar."
        )

    client = _client()
    response = client.beta.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=16000,
        # Adaptativo: leer un rótulo es una tarea visual con criterio
        # (distinguir un rótulo de campo de su valor), no una transcripción.
        thinking={"type": "adaptive"},
        system=build_system_prompt(variables),
        output_config={
            "effort": "high",
            "format": {
                "type": "json_schema",
                "schema": build_schema(variables),
            },
        },
        # Si un clasificador de seguridad rechaza la petición, la API reintenta
        # sola con otro modelo dentro de la misma llamada, en vez de devolver
        # nada. Va por `extra_body` y no como argumento nombrado porque el SDK
        # 0.76 todavía no lo expone en la firma de `create()`: pasarlo directo
        # da TypeError. Cuando el SDK lo incorpore, esto pasa a ser
        # `fallbacks="default"`.
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default"},
        messages=[
            {
                "role": "user",
                "content": [
                    _document_block(document),
                    {
                        "type": "text",
                        "text": (
                            "Analizá este rótulo y devolvé su estructura como "
                            "plantilla reutilizable."
                        ),
                    },
                ],
            }
        ],
    )

    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        reason = getattr(detail, "explanation", None) or "sin detalle"
        raise AgentError(
            f"El modelo no procesó esta imagen por motivos de seguridad ({reason}). "
            "Verificá que la foto sea efectivamente un rótulo de encomienda."
        )

    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise AgentError("El modelo no devolvió contenido para esta imagen.")

    usage = {
        "input": response.usage.input_tokens,
        "output": response.usage.output_tokens,
    }
    return json.loads(text), usage, response._request_id


def _error_message(exc):
    """Traduce una excepción del SDK a algo accionable para el usuario."""
    if isinstance(exc, anthropic.AuthenticationError):
        return "La ANTHROPIC_API_KEY configurada no es válida."
    # Sin saldo la API devuelve un 400 genérico, que sin traducir llega al
    # usuario como un volcado del error crudo. Es la falla más fácil de
    # confundir con un bug del sistema, y la única que se arregla en otro lado.
    if "credit balance is too low" in str(getattr(exc, "message", "")):
        return (
            "La cuenta de Anthropic se quedó sin saldo. Cargá créditos en "
            "console.anthropic.com (Plans & Billing) y volvé a intentar."
        )
    if isinstance(exc, anthropic.RateLimitError):
        return "Se alcanzó el límite de peticiones al modelo. Probá de nuevo en un minuto."
    if isinstance(exc, anthropic.APIConnectionError):
        return "No se pudo conectar con la API de Claude. Revisá la conexión."
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return "La API de Claude tuvo un error temporal. Probá de nuevo."
    return f"Error al procesar el rótulo: {getattr(exc, 'message', str(exc))}"


def _mark_error(label_import, message):
    label_import.status = LabelImportStatus.ERROR
    label_import.error = message
    label_import.finished_at = timezone.now()
    label_import.save()
    return label_import


# ---------------------------------------------------------------------------
# Conversión: de lo que devuelve el modelo (porcentajes) al formato de la API
# de element-layouts (milímetros).
# ---------------------------------------------------------------------------


def build_proposal(response, document):
    """Convierte la respuesta del modelo en un cuerpo para ``POST /element-layouts/``.

    El resultado se puede mandar tal cual a la API de layouts. Se agrega
    ``_revision`` con lo que el usuario necesita para controlar la lectura
    (confidence, notas, valores detectados, elementos descartados); la clave
    empieza con guión bajo para dejar claro que no es parte del cuerpo que
    acepta ``ElementLayoutSerializer`` y hay que quitarla antes de enviarlo.
    """
    width_mm = _positive(response.get("width_mm"), DEFAULT_WIDTH_MM)
    height_mm = _positive(response.get("height_mm"), DEFAULT_HEIGHT_MM)

    elements = []
    discarded = []
    detected_values = []

    for raw in response.get("elements") or []:
        converted, reason = _convert_element(raw, width_mm, height_mm)
        if converted is None:
            discarded.append({"element": raw, "reason": reason})
            continue
        elements.append(converted)

        if raw.get("detected_value"):
            detected_values.append(
                {"variable": raw.get("variable"), "value": raw["detected_value"]}
            )

    return {
        "name": f"Rótulo importado de {document.original_filename}",
        "description": (
            "Plantilla propuesta a partir de una foto. Revisá posiciones y "
            "campos antes de guardarla."
        ),
        "width_mm": round(width_mm, 2),
        "height_mm": round(height_mm, 2),
        "dpi": 300,
        "orientation": (
            response.get("orientation")
            or ("vertical" if height_mm >= width_mm else "horizontal")
        ),
        "metadata": {"origen": "importacion", "documento_id": document.pk},
        "elements": elements,
        "_revision": {
            "confidence": response.get("confidence"),
            "notes": response.get("notes") or "",
            "detected_values": detected_values,
            "discarded": discarded,
        },
    }


def _convert_element(raw, width_mm, height_mm):
    """Convierte un elemento. Devuelve ``(dict, None)`` o ``(None, reason)``."""
    element_type = raw.get("element_type")
    if element_type not in ("variable", "texto_estatico", "linea", "recuadro"):
        return None, f"tipo desconocido: {element_type!r}"

    # Coherencia tipo/variable/contenido. Es la misma regla que hace valer
    # `LayoutElement.clean()`; se aplica acá para no armar una propuesta
    # que la API de layouts va a rechazar entera por un elemento malo.
    variable = raw.get("variable") if element_type == "variable" else None
    content = (raw.get("content") or "") if element_type == "texto_estatico" else ""

    if element_type == "variable" and not variable:
        return None, "elemento de tipo variable sin variable asignada"
    if element_type == "texto_estatico" and not content.strip():
        return None, "texto estático sin contenido"

    element = {
        "element_type": element_type,
        "variable": variable,
        "content": content,
        "x_mm": _to_mm(raw.get("x_pct"), width_mm, minimum=0),
        "y_mm": _to_mm(raw.get("y_pct"), height_mm, minimum=0),
        "width_mm": _to_mm(raw.get("width_pct"), width_mm, minimum=MIN_MM),
        "height_mm": _to_mm(raw.get("height_pct"), height_mm, minimum=MIN_MM),
        "style": _convert_style(raw, element_type),
        "order": BACKGROUND_ORDER if element_type in ("linea", "recuadro") else CONTENT_ORDER,
    }
    return element, None


def _convert_style(raw, element_type):
    """Arma el JSON de estilo, con solo las claves que aplican a este tipo.

    Se omiten los valores que coinciden con ``DEFAULT_STYLE``: un estilo
    vacío ya significa "todo por defecto", así que guardarlos sería ruido.
    """
    style = {}

    color = raw.get("color")
    if isinstance(color, str) and color != DEFAULT_STYLE["color"]:
        style["color"] = color

    if element_type in ("linea", "recuadro"):
        style["grosor_mm"] = DEFAULT_STROKE_WIDTH_MM
        return style

    font_size = raw.get("tamano_pt")
    if isinstance(font_size, (int, float)) and font_size > 0:
        style["tamano_pt"] = round(float(font_size), 1)

    alignment = raw.get("alineacion")
    if alignment and alignment != DEFAULT_STYLE["alineacion"]:
        style["alineacion"] = alignment

    if raw.get("negrita"):
        style["negrita"] = True

    return style


def _to_mm(percent, dimension_mm, minimum):
    """Convierte un porcentaje de una dimensión a milímetros, acotado."""
    try:
        value = float(percent) / 100 * dimension_mm
    except (TypeError, ValueError):
        value = minimum
    # Se acota al rango que aceptan los validadores del modelo: nunca negativo,
    # y nunca cero donde se exige un tamaño mínimo.
    return round(max(value, minimum), 2)


def _positive(value, default):
    """Devuelve ``value`` si es un número positivo; si no, ``default``."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if number > 0 else float(default)
