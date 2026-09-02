"""Esquema y prompt con los que se le pide a Claude que lea un rótulo.

Este módulo es el otro extremo del contrato que ``apps.labels.estilos`` dejó
declarado: el importador es quien **produce** el JSON de estilo que el motor de
impresión después consume.

La idea central es que **el esquema se construye en tiempo de ejecución a
partir del catálogo**, no se escribe a mano. Cuando un administrador da de alta
la variable ``numero_bulto``, el próximo rótulo que se importe ya puede
detectarla, sin tocar una línea de código. Si el esquema fuera una constante,
el catálogo dinámico no serviría de nada: se podrían crear variables que el
importador nunca reconocería.

De ahí también que ``VariableRotulo.descripcion`` sea funcional y no
decorativa: es literalmente el texto que le explica al modelo qué representa
cada campo. Las descripciones sembradas por la migración están escritas para
que se lean bien acá dentro.

**Las posiciones se piden en porcentaje, no en milímetros.** Una foto no tiene
milímetros: tiene píxeles, y con la perspectiva y el recorte de la cámara
cualquier medida absoluta que el modelo estime va a estar mal. En cambio "el
destinatario empieza al 10% del ancho y al 30% del alto" es una observación
que se puede hacer mirando la imagen y que sobrevive a cualquier resolución.
La conversión a milímetros se hace acá, con las dimensiones físicas del rótulo.
"""

from apps.labels.estilos import ALINEACIONES

# Tipos de elemento que el modelo puede proponer. Es el mismo conjunto de
# `apps.labels.models.TipoElemento`, pero se declara acá como literales porque
# viaja dentro de un JSON Schema y no como choices de Django.
TIPOS_ELEMENTO = ("variable", "texto_estatico", "linea", "recuadro")

# Si el modelo no puede estimar el tamaño físico del rótulo, se asume el más
# común en encomiendas. El usuario lo corrige en el editor.
ANCHO_MM_POR_DEFECTO = 100
ALTO_MM_POR_DEFECTO = 150


def construir_esquema(variables):
    """Arma el JSON Schema de la respuesta a partir del catálogo activo.

    ``variables`` es un iterable de ``VariableRotulo``. Sus códigos forman el
    enum del campo ``variable``, así que el modelo no puede inventar uno que no
    exista en la base: o elige del catálogo, o marca el elemento como texto
    estático.
    """
    codigos = [v.codigo for v in variables]

    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "ancho_mm",
            "alto_mm",
            "orientacion",
            "confianza",
            "notas",
            "elementos",
        ],
        "properties": {
            "ancho_mm": {
                "type": "number",
                "description": (
                    "Ancho físico estimado del rótulo en milímetros. Los "
                    "tamaños habituales son 100x150, 100x100 y 148x210 (A5). "
                    "Si no hay forma de estimarlo, usar 100."
                ),
            },
            "alto_mm": {
                "type": "number",
                "description": (
                    "Alto físico estimado del rótulo en milímetros. Si no hay "
                    "forma de estimarlo, usar 150."
                ),
            },
            "orientacion": {
                "type": "string",
                "enum": ["vertical", "horizontal"],
                "description": "vertical si el alto supera al ancho.",
            },
            "confianza": {
                "type": "number",
                "description": (
                    "Qué tan confiable es esta lectura, de 0 a 1. Bajar el "
                    "valor si la foto está borrosa, cortada o muy inclinada."
                ),
            },
            "notas": {
                "type": "string",
                "description": (
                    "Observaciones para la persona que va a revisar: qué "
                    "quedó dudoso, qué no se llegó a leer, qué se asumió. "
                    "Cadena vacía si no hay nada que aclarar."
                ),
            },
            "elementos": {
                "type": "array",
                "description": (
                    "Todo lo que se ve dibujado en el rótulo, en orden de "
                    "lectura (de arriba hacia abajo, de izquierda a derecha)."
                ),
                "items": _esquema_elemento(codigos),
            },
        },
    }


def _esquema_elemento(codigos):
    """Esquema de un elemento suelto dentro del rótulo."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "tipo",
            "variable",
            "contenido",
            "valor_detectado",
            "x_pct",
            "y_pct",
            "ancho_pct",
            "alto_pct",
            "tamano_pt",
            "alineacion",
            "negrita",
            "color",
        ],
        "properties": {
            "tipo": {
                "type": "string",
                "enum": list(TIPOS_ELEMENTO),
                "description": (
                    "variable: un dato que cambia en cada envío (un nombre, "
                    "una dirección, un QR). texto_estatico: un texto impreso "
                    "que sería igual en todos los rótulos de esta plantilla "
                    "(rótulos de campo como 'DESTINATARIO:', avisos como "
                    "'FRÁGIL'). linea: una línea divisoria. recuadro: un "
                    "marco o caja dibujada."
                ),
            },
            "variable": {
                "type": ["string", "null"],
                "enum": codigos + [None],
                "description": (
                    "Solo cuando tipo es 'variable': el código del catálogo "
                    "que corresponde a este dato. null en cualquier otro caso. "
                    "Si un dato variable no encaja en ninguno de los códigos "
                    "disponibles, no lo fuerces: marcá el elemento como "
                    "texto_estatico y explicá en 'notas' qué campo faltaría."
                ),
            },
            "contenido": {
                "type": ["string", "null"],
                "description": (
                    "Solo cuando tipo es 'texto_estatico': el texto literal "
                    "tal como está impreso. null en cualquier otro caso."
                ),
            },
            "valor_detectado": {
                "type": ["string", "null"],
                "description": (
                    "Solo cuando tipo es 'variable': el valor concreto que se "
                    "lee en ESTA foto (por ejemplo el nombre real del "
                    "destinatario). No forma parte de la plantilla — sirve "
                    "para que quien revisa verifique que el campo se "
                    "identificó bien. null en cualquier otro caso."
                ),
            },
            "x_pct": {
                "type": "number",
                "description": (
                    "Borde izquierdo del elemento, como porcentaje del ancho "
                    "del rótulo (0 = borde izquierdo, 100 = borde derecho)."
                ),
            },
            "y_pct": {
                "type": "number",
                "description": (
                    "Borde superior del elemento, como porcentaje del alto "
                    "del rótulo (0 = arriba de todo, 100 = abajo de todo)."
                ),
            },
            "ancho_pct": {
                "type": "number",
                "description": "Ancho del elemento como porcentaje del ancho del rótulo.",
            },
            "alto_pct": {
                "type": "number",
                "description": (
                    "Alto del elemento como porcentaje del alto del rótulo. "
                    "Para una línea, usar un valor mínimo (0.2)."
                ),
            },
            "tamano_pt": {
                "type": ["number", "null"],
                "description": (
                    "Tamaño de letra estimado en puntos, para elementos con "
                    "texto. Referencia: 8 es letra chica de rótulo de campo, "
                    "10-12 texto normal, 16 o más un destinatario destacado. "
                    "null para líneas, recuadros, QR e imágenes."
                ),
            },
            "alineacion": {
                "type": ["string", "null"],
                "enum": list(ALINEACIONES) + [None],
                "description": "Alineación del texto. null si el elemento no lleva texto.",
            },
            "negrita": {
                "type": "boolean",
                "description": "Si el texto se ve en negrita. false si no lleva texto.",
            },
            "color": {
                "type": "string",
                "description": (
                    "Color del texto o del trazo en formato #rrggbb. La "
                    "enorme mayoría de los rótulos son negros: usar #000000 "
                    "salvo que se vea claramente otro color."
                ),
            },
        },
    }


def construir_system_prompt(variables):
    """Arma las instrucciones del sistema con el catálogo disponible.

    El catálogo se lista con código, etiqueta y descripción. La descripción es
    lo que permite distinguir campos que se parecen —el remitente del
    destinatario es la confusión clásica— y por eso se incluye entera.
    """
    catalogo = "\n".join(
        f"- `{v.codigo}` ({v.get_tipo_dato_display()}) — {v.etiqueta}: "
        f"{v.descripcion or 'sin descripción'}"
        for v in variables
    )

    return f"""Sos un asistente que analiza fotos de rótulos de encomienda para \
reconstruir su diseño como una plantilla reutilizable.

Un rótulo es la etiqueta que se pega en un paquete. Tu trabajo NO es transcribir \
los datos de este envío puntual: es identificar la ESTRUCTURA del rótulo, para \
que después se pueda imprimir el mismo diseño con los datos de cualquier otro \
envío.

La distinción más importante que tenés que hacer en cada cosa que veas es:

- ¿Es un DATO QUE CAMBIA en cada envío? (el nombre de quien recibe, una \
dirección, un código QR, un número de pedido) → tipo `variable`, y elegís del \
catálogo de abajo el código que le corresponde.
- ¿Es TEXTO IMPRESO que sería igual en todos los rótulos de esta plantilla? \
(la palabra "DESTINATARIO:" que precede al nombre, un aviso como "FRÁGIL", el \
nombre fijo de la empresa transportista) → tipo `texto_estatico`.
- ¿Es una línea divisoria o un marco? → tipo `linea` o `recuadro`.

Un error frecuente es marcar como texto estático el valor de un campo. Si ves \
"DESTINATARIO: Juan Pérez", eso son DOS elementos: el texto estático \
"DESTINATARIO:" y, al lado o debajo, la variable `destinatario`.

## Catálogo de variables disponibles

{catalogo}

Elegí siempre el código cuya descripción mejor coincida con lo que ves. Si un \
dato variable no encaja en ninguno, no lo fuerces a la variable más parecida: \
marcalo como `texto_estatico` y aclaralo en `notas`, así un administrador puede \
dar de alta el campo que falta.

## Posiciones

Todas las posiciones van en porcentaje del ancho y del alto del rótulo, no en \
milímetros, y se miden desde la esquina superior izquierda. Tomá como \
referencia los bordes del rótulo (el papel), no los bordes de la foto: si la \
foto tiene fondo alrededor, ignoralo. Si el rótulo está inclinado, estimá las \
posiciones como si estuviera derecho.

Sé prolijo con las posiciones: es lo que más trabajo le ahorra a quien revisa. \
Elementos que están alineados entre sí en la foto deberían compartir el mismo \
`x_pct`.

## Qué NO hacer

- No inventes elementos que no se ven en la foto.
- No omitas líneas ni recuadros: son los que le dan la estructura visual al \
rótulo y sin ellos el diseño impreso no se parece al original.
- Si una parte de la foto está cortada, borrosa o ilegible, no adivines: \
bajá `confianza` y explicá en `notas` qué no pudiste leer."""
