"""Traduce lo que Claude leyó de un rótulo al ``design`` de una plantilla.

La lectura de la foto (``agent.build_proposal``) queda en el formato de los
diseños por elementos (Sistema 2: milímetros, ``LayoutElement``), que ningún
flujo de impresión de pedidos usa. Esto la pasa al formato de
``LabelTemplate`` (Sistema 1: porcentajes del rótulo), para abrirla en
editor_rotulos.html como una plantilla nueva (2026-10-30, plan
docs/superpowers/plans/2026-10-30-foto-a-plantilla.md).

No guarda nada: la plantilla se crea cuando el comerciante la guarda desde el
editor. Lo que no tiene lugar en una plantilla (un dato repetido, una variable
que el editor no conoce) vuelve en ``omitted`` con el motivo, para que se vea.
"""

from apps.labels.serializers import (
    BARCODE_HEIGHT_MAX,
    BARCODE_HEIGHT_MIN,
    BARCODE_WIDTH_MAX,
    BARCODE_WIDTH_MIN,
    FONT_SIZE_MAX,
    FONT_SIZE_MIN,
    QR_SIZE_MAX,
    QR_SIZE_MIN,
)

# Variable del catálogo → campo de texto del editor.
TEXT_FIELDS = {
    "remitente": "remitente",
    "destinatario": "destinatario",
    "domicilio": "domicilio",
    "codigo_postal": "cp",
    "localidad_provincia": "localidad",
    "numero_pedido": "pedido",
}
# Variables que en la plantilla son un texto fijo con un marcador.
MARKER_TEXTS = {"numero_seguimiento": "{{tracking}}"}
ALIGNMENTS = {"centro": "center", "derecha": "right"}
# Los mismos límites que una plantilla (LabelTemplateSerializer).
WIDTH_CM_RANGE = (5.0, 30.0)
HEIGHT_CM_RANGE = (5.0, 40.0)
DEFAULT_SIZE_MM = (100.0, 150.0)
STROKE = "#000000"


def clamp(value, low, high):
    return max(low, min(high, value))


def proposal_to_template(proposal):
    """``{name, width_cm, height_cm, design, omitted}`` a partir de una
    propuesta guardada en ``LabelImport.proposal``."""
    width_mm = _positive(proposal.get("width_mm"), DEFAULT_SIZE_MM[0])
    height_mm = _positive(proposal.get("height_mm"), DEFAULT_SIZE_MM[1])
    builder = _Builder(width_mm, height_mm)
    for raw in proposal.get("elements") or []:
        builder.add(raw)
    return {
        "name": (proposal.get("name") or "Rótulo importado de una foto")[:150],
        "width_cm": round(clamp(width_mm / 10, *WIDTH_CM_RANGE), 1),
        "height_cm": round(clamp(height_mm / 10, *HEIGHT_CM_RANGE), 1),
        "design": builder.design,
        "omitted": builder.omitted,
    }


class _Builder:
    def __init__(self, width_mm, height_mm):
        self.width_mm = width_mm
        self.height_mm = height_mm
        self.design = {}
        self.omitted = []

    # --- posiciones -------------------------------------------------------
    def box(self, raw):
        """``(left, top, width, height)`` en % del rótulo, dentro de 0-100."""
        left = clamp(_number(raw.get("x_mm")) / self.width_mm * 100, 0, 100)
        top = clamp(_number(raw.get("y_mm")) / self.height_mm * 100, 0, 100)
        width = clamp(_number(raw.get("width_mm")) / self.width_mm * 100, 0, 100 - left)
        height = clamp(_number(raw.get("height_mm")) / self.height_mm * 100, 0, 100 - top)
        return round(left, 1), round(top, 1), round(width, 1), round(height, 1)

    def cm(self, raw, key):
        return _number(raw.get(key)) / 10

    def skip(self, raw, reason):
        what = raw.get("variable") or raw.get("content") or raw.get("element_type") or "elemento"
        self.omitted.append({"label": str(what)[:80], "reason": reason})

    # --- elementos --------------------------------------------------------
    def add(self, raw):
        kind = raw.get("element_type")
        if kind == "variable":
            self.add_variable(raw)
        elif kind == "texto_estatico":
            if (raw.get("content") or "").strip():
                self.add_text(raw, raw["content"].strip())
            else:
                self.skip(raw, "texto fijo vacío")
        elif kind in ("linea", "recuadro"):
            self.add_shape(raw, kind)
        else:
            self.skip(raw, "tipo de elemento desconocido")

    def add_variable(self, raw):
        variable = raw.get("variable")
        left, top, width, height = self.box(raw)
        if variable in TEXT_FIELDS:
            field = TEXT_FIELDS[variable]
            if field in self.design:
                self.skip(raw, "ya había otro igual: quedó el primero")
                return
            # Solo el dato, como las plantillas de la galería: las etiquetas
            # ("CP:", "DESTINATARIO") llegan de la foto como textos fijos.
            entry = {"left": left, "top": top, "text": f"{{{{{field}}}}}"}
            if 1 <= width < 100:
                entry["width"] = width
            entry.update(_text_style(raw.get("style") or {}))
            self.design[field] = entry
        elif variable in MARKER_TEXTS:
            self.add_text(raw, MARKER_TEXTS[variable])
        elif variable == "logo_empresa":
            self.single(raw, "logo", {"left": left, "top": top})
        elif variable in ("qr", "qr_envio"):
            side = min(self.cm(raw, "width_mm"), self.cm(raw, "height_mm"))
            self.single(raw, "qr", {"left": left, "top": top, "size": round(clamp(side, QR_SIZE_MIN, QR_SIZE_MAX), 1)})
        elif variable == "codigo_barras":
            self.single(raw, "barcode", {
                "left": left,
                "top": top,
                "width": round(clamp(self.cm(raw, "width_mm"), BARCODE_WIDTH_MIN, BARCODE_WIDTH_MAX), 1),
                "height": round(clamp(self.cm(raw, "height_mm"), BARCODE_HEIGHT_MIN, BARCODE_HEIGHT_MAX), 1),
            })
        else:
            self.skip(raw, "el editor de rótulos no tiene este dato")

    def single(self, raw, key, entry):
        if key in self.design:
            self.skip(raw, "ya había otro igual: quedó el primero")
        else:
            self.design[key] = entry

    def add_text(self, raw, text):
        left, top, width, _ = self.box(raw)
        entry = {"text": text, "left": left, "top": top}
        if 1 <= width < 100:
            entry["width"] = width
        entry.update(_text_style(raw.get("style") or {}))
        self.design.setdefault("texts", []).append(entry)

    def add_shape(self, raw, kind):
        left, top, width, height = self.box(raw)
        shapes = self.design.setdefault("shapes", [])
        shape = {"id": f"s{len(shapes) + 1}", "left": left, "top": top}
        if kind == "linea":
            # Una línea del editor es horizontal (alto 0) o vertical (ancho 0).
            wide = _number(raw.get("width_mm")) >= _number(raw.get("height_mm"))
            shape.update({"kind": "line", "width": width if wide else 0, "height": 0 if wide else height, "stroke": STROKE})
            if not (shape["width"] or shape["height"]):
                self.skip(raw, "línea sin largo")
                return
        else:
            if not (width and height):
                self.skip(raw, "recuadro sin tamaño")
                return
            shape.update({"kind": "rect", "width": width, "height": height, "fill": None, "stroke": STROKE})
        shapes.append(shape)


def _text_style(style):
    entry = {}
    size = style.get("tamano_pt")
    if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0:
        entry["font_size"] = round(clamp(float(size), FONT_SIZE_MIN, FONT_SIZE_MAX), 1)
    if style.get("negrita"):
        entry["bold"] = True
    align = ALIGNMENTS.get(style.get("alineacion"))
    if align:
        entry["align"] = align
    return entry


def _number(value):
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _positive(value, default):
    number = _number(value)
    return number if number > 0 else default
