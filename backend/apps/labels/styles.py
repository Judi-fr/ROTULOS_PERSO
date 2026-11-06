"""Valores de estilo que usa la lectura de fotos (``apps.processing``).

La lectura propone los elementos con un ``style`` de claves en español
(``tamano_pt``, ``alineacion``, ``negrita``, ...) y ``to_template`` lo traduce
al ``design`` de la plantilla. Acá solo quedan los valores posibles de la
alineación y los que se toman por defecto: lo que se aparta de ellos es lo único
que se guarda en la propuesta.
"""



ALIGNMENTS = ("izquierda", "centro", "derecha", "justificado")

DEFAULT_STYLE = {
    "tamano_pt": 10,
    "alineacion": "izquierda",
    "negrita": False,
    "cursiva": False,
    "fuente": None,
    "color": "#000000",
    "grosor_mm": 0.3,
}
