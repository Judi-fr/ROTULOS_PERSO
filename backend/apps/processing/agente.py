"""Agente que lee un rótulo desde una foto y propone una plantilla.

Flujo completo:

1. Se arma el esquema y las instrucciones a partir del catálogo activo
   (``esquema.py``) — así una variable dada de alta hoy ya se puede detectar.
2. Se le manda la imagen (o el PDF) a Claude con *structured outputs*, que
   obliga a la respuesta a validar contra ese esquema. No hace falta parsear
   texto libre ni rezar para que el JSON venga bien formado.
3. Se convierte el resultado —que viene en porcentajes— al formato exacto que
   espera ``PlantillaSerializer``, en milímetros.

El paso 3 no es un detalle: la conversión también **descarta** los elementos
que violarían las reglas del modelo de datos (un texto estático sin texto, una
variable sin variable). Es preferible entregar una propuesta con un elemento
de menos, que el usuario agrega a mano, que una que el POST a
``/plantillas/`` va a rechazar entera.
"""

import base64
import json
import logging

import anthropic
from django.conf import settings
from django.utils import timezone

from apps.labels.estilos import ESTILO_POR_DEFECTO
from apps.labels.models import VariableRotulo

from .esquema import (
    ALTO_MM_POR_DEFECTO,
    ANCHO_MM_POR_DEFECTO,
    construir_esquema,
    construir_system_prompt,
)
from .models import EstadoImportacion

logger = logging.getLogger(__name__)

# Capas de dibujado: las líneas y los recuadros van al fondo para que el texto
# quede encima. Coincide con la semántica de `ElementoPlantilla.orden`.
ORDEN_FONDO = 5
ORDEN_CONTENIDO = 20

# Grosor de trazo por defecto para líneas y recuadros. No se le pide al modelo
# porque no es algo que se pueda estimar de una foto con ninguna precisión.
GROSOR_MM_POR_DEFECTO = ESTILO_POR_DEFECTO["grosor_mm"]

# Tamaños mínimos que exige `ElementoPlantilla` (MinValueValidator).
MIN_MM = 0.01


class ErrorDeAgente(Exception):
    """Falla al interpretar un rótulo. El mensaje es apto para mostrar al usuario."""


def _cliente():
    """Devuelve el cliente de Anthropic, o falla con un mensaje claro."""
    if not settings.ANTHROPIC_API_KEY:
        raise ErrorDeAgente(
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


def _bloque_del_documento(documento):
    """Arma el bloque de contenido con el archivo, según sea imagen o PDF."""
    with documento.archivo.open("rb") as f:
        datos = base64.standard_b64encode(f.read()).decode("utf-8")

    if documento.es_imagen:
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": documento.tipo_mime,
                "data": datos,
            },
        }
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": datos,
        },
    }


def interpretar(importacion):
    """Procesa una importación de punta a punta y la deja guardada.

    Muta y guarda ``importacion``: al terminar queda en estado ``completada``
    (con ``propuesta``) o ``error`` (con ``error``). No lanza excepción por una
    falla del modelo — el error queda registrado en la fila, que es lo que la
    vista devuelve.
    """
    importacion.estado = EstadoImportacion.PROCESANDO
    importacion.modelo = settings.ANTHROPIC_MODEL
    importacion.save(update_fields=["estado", "modelo"])

    try:
        respuesta, uso, request_id = _pedir_lectura(importacion.documento)
    except ErrorDeAgente as exc:
        return _marcar_error(importacion, str(exc))
    except anthropic.APIError as exc:
        # El detalle técnico va al log; al usuario se le dice qué puede hacer.
        logger.exception("Falló la lectura del rótulo %s", importacion.pk)
        return _marcar_error(importacion, _mensaje_de_error(exc))
    except ValueError as exc:
        # JSONDecodeError es un ValueError. Con structured outputs no debería
        # pasar, pero la vista promete no lanzar: si escapara, sería un 500 en
        # lugar de una importación en estado `error`.
        logger.exception("JSON inválido en la importación %s", importacion.pk)
        return _marcar_error(
            importacion, f"El modelo devolvió una respuesta ilegible: {exc}"
        )

    importacion.respuesta_cruda = respuesta
    importacion.tokens_entrada = uso.get("entrada", 0)
    importacion.tokens_salida = uso.get("salida", 0)
    importacion.request_id = request_id or ""

    try:
        importacion.propuesta = construir_propuesta(respuesta, importacion.documento)
    except (KeyError, TypeError, ValueError) as exc:
        logger.exception("Respuesta inesperada en la importación %s", importacion.pk)
        return _marcar_error(
            importacion,
            "El modelo devolvió una respuesta que no se pudo interpretar. "
            f"Detalle: {exc}",
        )

    importacion.estado = EstadoImportacion.COMPLETADA
    importacion.finalizada_en = timezone.now()
    importacion.save()
    return importacion


def _pedir_lectura(documento):
    """Llama al modelo. Devuelve ``(respuesta_dict, uso, request_id)``."""
    variables = list(VariableRotulo.objects.filter(activa=True))
    if not variables:
        raise ErrorDeAgente(
            "El catálogo de variables está vacío: no hay campos que detectar. "
            "Aplicá las migraciones o dá de alta variables antes de importar."
        )

    cliente = _cliente()
    respuesta = cliente.beta.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=16000,
        # Adaptativo: leer un rótulo es una tarea visual con criterio
        # (distinguir un rótulo de campo de su valor), no una transcripción.
        thinking={"type": "adaptive"},
        system=construir_system_prompt(variables),
        output_config={
            "effort": "high",
            "format": {
                "type": "json_schema",
                "schema": construir_esquema(variables),
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
                    _bloque_del_documento(documento),
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

    if respuesta.stop_reason == "refusal":
        detalle = getattr(respuesta, "stop_details", None)
        motivo = getattr(detalle, "explanation", None) or "sin detalle"
        raise ErrorDeAgente(
            f"El modelo no procesó esta imagen por motivos de seguridad ({motivo}). "
            "Verificá que la foto sea efectivamente un rótulo de encomienda."
        )

    texto = next((b.text for b in respuesta.content if b.type == "text"), None)
    if not texto:
        raise ErrorDeAgente("El modelo no devolvió contenido para esta imagen.")

    uso = {
        "entrada": respuesta.usage.input_tokens,
        "salida": respuesta.usage.output_tokens,
    }
    return json.loads(texto), uso, respuesta._request_id


def _mensaje_de_error(exc):
    """Traduce una excepción del SDK a algo accionable para el usuario."""
    if isinstance(exc, anthropic.AuthenticationError):
        return "La ANTHROPIC_API_KEY configurada no es válida."
    if isinstance(exc, anthropic.RateLimitError):
        return "Se alcanzó el límite de peticiones al modelo. Probá de nuevo en un minuto."
    if isinstance(exc, anthropic.APIConnectionError):
        return "No se pudo conectar con la API de Claude. Revisá la conexión."
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return "La API de Claude tuvo un error temporal. Probá de nuevo."
    return f"Error al procesar el rótulo: {getattr(exc, 'message', str(exc))}"


def _marcar_error(importacion, mensaje):
    importacion.estado = EstadoImportacion.ERROR
    importacion.error = mensaje
    importacion.finalizada_en = timezone.now()
    importacion.save()
    return importacion


# ---------------------------------------------------------------------------
# Conversión: de lo que devuelve el modelo (porcentajes) al formato de la API
# de plantillas (milímetros).
# ---------------------------------------------------------------------------


def construir_propuesta(respuesta, documento):
    """Convierte la respuesta del modelo en un cuerpo para ``POST /plantillas/``.

    El resultado se puede mandar tal cual a la API de plantillas. Se agrega
    ``_revision`` con lo que el usuario necesita para controlar la lectura
    (confianza, notas, valores detectados, elementos descartados); la clave
    empieza con guión bajo para dejar claro que no es parte del cuerpo que
    acepta ``PlantillaSerializer`` y hay que quitarla antes de enviarlo.
    """
    ancho_mm = _positivo(respuesta.get("ancho_mm"), ANCHO_MM_POR_DEFECTO)
    alto_mm = _positivo(respuesta.get("alto_mm"), ALTO_MM_POR_DEFECTO)

    elementos = []
    descartados = []
    detectados = []

    for crudo in respuesta.get("elementos") or []:
        convertido, motivo = _convertir_elemento(crudo, ancho_mm, alto_mm)
        if convertido is None:
            descartados.append({"elemento": crudo, "motivo": motivo})
            continue
        elementos.append(convertido)

        if crudo.get("valor_detectado"):
            detectados.append(
                {"variable": crudo.get("variable"), "valor": crudo["valor_detectado"]}
            )

    return {
        "nombre": f"Rótulo importado de {documento.nombre_original}",
        "descripcion": (
            "Plantilla propuesta a partir de una foto. Revisá posiciones y "
            "campos antes de guardarla."
        ),
        "ancho_mm": round(ancho_mm, 2),
        "alto_mm": round(alto_mm, 2),
        "dpi": 300,
        "orientacion": (
            respuesta.get("orientacion")
            or ("vertical" if alto_mm >= ancho_mm else "horizontal")
        ),
        "metadatos": {"origen": "importacion", "documento_id": documento.pk},
        "elementos": elementos,
        "_revision": {
            "confianza": respuesta.get("confianza"),
            "notas": respuesta.get("notas") or "",
            "valores_detectados": detectados,
            "descartados": descartados,
        },
    }


def _convertir_elemento(crudo, ancho_mm, alto_mm):
    """Convierte un elemento. Devuelve ``(dict, None)`` o ``(None, motivo)``."""
    tipo = crudo.get("tipo")
    if tipo not in ("variable", "texto_estatico", "linea", "recuadro"):
        return None, f"tipo desconocido: {tipo!r}"

    # Coherencia tipo/variable/contenido. Es la misma regla que hace valer
    # `ElementoPlantilla.clean()`; se aplica acá para no armar una propuesta
    # que la API de plantillas va a rechazar entera por un elemento malo.
    variable = crudo.get("variable") if tipo == "variable" else None
    contenido = (crudo.get("contenido") or "") if tipo == "texto_estatico" else ""

    if tipo == "variable" and not variable:
        return None, "elemento de tipo variable sin variable asignada"
    if tipo == "texto_estatico" and not contenido.strip():
        return None, "texto estático sin contenido"

    elemento = {
        "tipo": tipo,
        "variable": variable,
        "contenido": contenido,
        "x_mm": _a_mm(crudo.get("x_pct"), ancho_mm, minimo=0),
        "y_mm": _a_mm(crudo.get("y_pct"), alto_mm, minimo=0),
        "ancho_mm": _a_mm(crudo.get("ancho_pct"), ancho_mm, minimo=MIN_MM),
        "alto_mm": _a_mm(crudo.get("alto_pct"), alto_mm, minimo=MIN_MM),
        "estilo": _convertir_estilo(crudo, tipo),
        "orden": ORDEN_FONDO if tipo in ("linea", "recuadro") else ORDEN_CONTENIDO,
    }
    return elemento, None


def _convertir_estilo(crudo, tipo):
    """Arma el JSON de estilo, con solo las claves que aplican a este tipo.

    Se omiten los valores que coinciden con ``ESTILO_POR_DEFECTO``: un estilo
    vacío ya significa "todo por defecto", así que guardarlos sería ruido.
    """
    estilo = {}

    color = crudo.get("color")
    if isinstance(color, str) and color != ESTILO_POR_DEFECTO["color"]:
        estilo["color"] = color

    if tipo in ("linea", "recuadro"):
        estilo["grosor_mm"] = GROSOR_MM_POR_DEFECTO
        return estilo

    tamano = crudo.get("tamano_pt")
    if isinstance(tamano, (int, float)) and tamano > 0:
        estilo["tamano_pt"] = round(float(tamano), 1)

    alineacion = crudo.get("alineacion")
    if alineacion and alineacion != ESTILO_POR_DEFECTO["alineacion"]:
        estilo["alineacion"] = alineacion

    if crudo.get("negrita"):
        estilo["negrita"] = True

    return estilo


def _a_mm(porcentaje, dimension_mm, minimo):
    """Convierte un porcentaje de una dimensión a milímetros, acotado."""
    try:
        valor = float(porcentaje) / 100 * dimension_mm
    except (TypeError, ValueError):
        valor = minimo
    # Se acota al rango que aceptan los validadores del modelo: nunca negativo,
    # y nunca cero donde se exige un tamaño mínimo.
    return round(max(valor, minimo), 2)


def _positivo(valor, por_defecto):
    """Devuelve ``valor`` si es un número positivo; si no, ``por_defecto``."""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return float(por_defecto)
    return numero if numero > 0 else float(por_defecto)
