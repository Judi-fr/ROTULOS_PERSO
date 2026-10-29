"""Galería de plantillas (fase 5 de
``docs/superpowers/specs/2026-10-28-editor-rotulos-galeria-design.md``).

Los ocho diseños que el comerciante elige al apretar "+ Nueva plantilla". Los
siete nuevos están armados SOLO con lo que el editor sabe hacer (formas,
cajas con texto, colores, tipografías, íconos, la imagen ``"logo"``): la regla
del usuario es que el editor pueda construir cada diseño de la galería. La
"Etiqueta de envío estándar" (A) ya existe y no se toca; solo va última.

Posiciones en % del rótulo de 10×15 cm (1 % horizontal = 1 mm, 1 % vertical =
1,5 mm). Los textos usan los marcadores de ``build_label_context``; nada de
productos, precios ni contacto del comprador.

La migración ``0014_gallery_templates`` copia ``GALLERY`` tal como está hoy:
si estos diseños cambian después, hace falta otra migración para las
plantillas ya sembradas.
"""

STANDARD_TEMPLATE_NAME = "Etiqueta de envío estándar"
STANDARD_GALLERY_ORDER = 8


def _rect(shape_id, left, top, width, height, **style):
    return {"id": shape_id, "kind": "rect", "left": left, "top": top, "width": width, "height": height, **style}


def _cell(shape_id, left, top, width, height, value, header=False, **text):
    """Una celda de la grilla B: recuadro con su texto."""
    style = {"fill": "#d4d4d4" if header else None, "stroke": "#111111", "stroke_width": 0.6}
    if value is None:
        return _rect(shape_id, left, top, width, height, **style)
    box_text = {"value": value, "align": "center", "valign": "middle", "padding": 1,
                "font_size": 7 if header else 9, "bold": not header, **text}
    if header:
        box_text.update({"uppercase": True, "letter_spacing": 0.8})
    return _rect(shape_id, left, top, width, height, **style, text=box_text)


def _line(shape_id, left, top, width=0, height=0, stroke="#e4e4e7", stroke_width=0.8, **style):
    return {"id": shape_id, "kind": "line", "left": left, "top": top, "width": width, "height": height,
            "stroke": stroke, "stroke_width": stroke_width, **style}


def _number(shape_id, left, top, number):
    """Círculo oscuro con un número (las secciones del diseño H)."""
    return {"id": shape_id, "kind": "ellipse", "left": left, "top": top, "width": 4.4, "height": 2.93,
            "fill": "primary", "text": {"value": str(number), "font_size": 7, "bold": True, "color": "#ffffff",
                                        "align": "center", "valign": "middle", "padding": 0}}


def _caption(left, top, text, color="#52525b", **extra):
    """Título chico en mayúsculas espaciadas."""
    return {"left": left, "top": top, "text": text, "font_size": 7, "bold": True, "uppercase": True,
            "letter_spacing": 1.2, "color": color, **extra}


# --- H · Moderno -------------------------------------------------------------
MODERN = {
    "palette": {"primary": "#18181b", "accent": "#a3e635"},
    "shapes": [
        _rect("barra", 0, 0, 100, 10, fill="primary", z=1),
        {"id": "logo", "kind": "image", "image": "logo", "left": 4, "top": 1.67, "width": 10, "height": 6.67,
         "fit": "contain", "z": 2},
        _number("n1", 5, 12.5, 1),
        _line("l1", 0, 35.5, width=100),
        _number("n2", 5, 37.5, 2),
        _line("l2", 50, 35.5, height=15),
        _number("n3", 54, 37.5, 3),
        _rect("servicio", 54, 42.5, 41, 5, fill="accent", radius=1.5, z=3,
              text={"value": "{{envio}}", "font_size": 8, "bold": True, "uppercase": True, "align": "center",
                    "valign": "middle", "padding": 1, "color": "#18181b", "hide_if_empty": True}),
        _line("l3", 0, 50.5, width=100),
        _number("n4", 5, 52.5, 4),
    ],
    "remitente": {"left": 17, "top": 3, "text": "{{remitente}}", "font_size": 12, "bold": True, "width": 52,
                  "shrink_to_fit": True, "color": "#fafafa"},
    "pedido": {"left": 70, "top": 3.4, "text": "{{pedido}}", "font_size": 10, "width": 26, "align": "right",
               "shrink_to_fit": True, "color": "#fafafa"},
    "destinatario": {"left": 5, "top": 16.5, "text": "{{destinatario}}", "font_size": 20, "bold": True,
                     "uppercase": True, "width": 90, "max_lines": 2, "line_height": 1.0},
    "domicilio": {"left": 5, "top": 27.6, "text": "{{domicilio}}", "font_size": 10, "width": 90,
                  "shrink_to_fit": True},
    "localidad": {"left": 5, "top": 30.9, "text": "{{localidad}}", "font_size": 10, "width": 90,
                  "shrink_to_fit": True},
    "cp": {"left": 5, "top": 41.5, "text": "{{cp}}", "font_size": 30, "bold": True, "width": 43},
    "texts": [
        _caption(11, 12.9, "Para"),
        _caption(11, 37.9, "Código postal"),
        _caption(60, 37.9, "Servicio"),
        _caption(11, 52.9, "Remitente"),
        {"left": 5, "top": 56.5, "text": "{{remitente_domicilio}}", "font_size": 9.5, "width": 90,
         "hide_if_empty": True, "shrink_to_fit": True},
        {"left": 5, "top": 59.8, "text": "Tel. {{remitente_telefono}}", "font_size": 9.5, "width": 90,
         "hide_if_empty": True},
        {"left": 5, "top": 63.5, "text": "{{referencia}}", "font_size": 9, "width": 90, "max_lines": 2,
         "hide_if_empty": True, "color": "#52525b"},
    ],
    "barcode": {"left": 5, "top": 77, "width": 6.2, "height": 2.0, "symbology": "code128", "data": "{{tracking}}",
                "show_text": True},
    "qr": {"left": 72, "top": 76.5, "size": 2.3, "data": "{{tracking_url}}"},
}

# --- D · Con la marca --------------------------------------------------------
BRAND = {
    "palette": {"primary": "#166534", "accent": "#dcfce7"},
    "shapes": [
        _rect("franja", 2, 1.33, 96, 16, fill="accent", radius=3, z=1),
        {"id": "logo", "kind": "image", "image": "logo", "left": 6, "top": 3.33, "width": 15, "height": 10,
         "fit": "cover", "circle": True, "stroke": "#ffffff", "stroke_width": 2, "z": 2},
        _rect("borde", 1, 0.67, 98, 98.66, fill=None, stroke="primary", stroke_width=1.5, radius=4, z=3),
        _rect("pastilla", 7, 39, 40, 6.5, fill="primary", radius=4.8, z=4),
    ],
    "remitente": {"left": 25, "top": 6.5, "text": "{{remitente}}", "font_size": 17, "font_family": "serif",
                  "italic": True, "width": 70, "shrink_to_fit": True, "color": "primary"},
    "destinatario": {"left": 7, "top": 24, "text": "{{destinatario}}", "font_size": 17, "font_family": "serif",
                     "width": 86, "max_lines": 2, "line_height": 1.05},
    "domicilio": {"left": 7, "top": 32.3, "text": "{{domicilio}}", "font_size": 10, "width": 86,
                  "shrink_to_fit": True},
    "localidad": {"left": 7, "top": 35.3, "text": "{{localidad}}", "font_size": 10, "width": 86,
                  "shrink_to_fit": True},
    "cp": {"left": 7, "top": 40.6, "text": "CP {{cp}}", "font_size": 13, "bold": True, "width": 40,
           "align": "center", "color": "#ffffff"},
    "pedido": {"left": 7, "top": 65, "text": "{{pedido}}", "font_size": 10, "bold": True, "width": 60},
    "texts": [
        _caption(7, 20.5, "Para", color="primary"),
        {"left": 7, "top": 49, "text": "¡Gracias por tu compra!", "font_size": 12, "font_family": "serif",
         "italic": True, "color": "primary"},
        {"left": 7, "top": 53.5, "text": "{{referencia}}", "font_size": 9, "width": 86, "max_lines": 2,
         "hide_if_empty": True},
        {"left": 7, "top": 59, "text": "De: {{remitente_domicilio}}", "font_size": 8.5, "width": 86,
         "hide_if_empty": True, "shrink_to_fit": True, "color": "#52525b"},
    ],
    "barcode": {"left": 7, "top": 69, "width": 6.0, "height": 2.0, "symbology": "code128", "data": "{{tracking}}",
                "show_text": True},
    "qr": {"left": 70, "top": 69, "size": 2.2, "data": "{{tracking_url}}"},
}

# --- C · Destacado -------------------------------------------------------------
HIGHLIGHT = {
    "palette": {"primary": "#1e3a8a", "accent": "#dc2626"},
    "shapes": [
        _rect("banda", 0, 0, 100, 11, fill="primary", z=1),
        _rect("banda_cp", 0, 36, 100, 10, fill="#111111", z=2),
        _rect("fragil", 5, 49, 32, 6, stroke="accent", stroke_width=1.5, radius=1.5, z=3,
              text={"value": "FRÁGIL", "font_size": 11, "bold": True, "color": "accent", "align": "center",
                    "valign": "middle", "letter_spacing": 1.5, "padding": 1}),
        {"id": "icono_fragil", "kind": "icon", "icon": "fragil", "left": 39, "top": 49, "width": 8, "height": 6,
         "fill": "accent", "z": 4},
    ],
    "remitente": {"left": 5, "top": 3, "text": "{{remitente}}", "font_size": 14, "bold": True, "width": 58,
                  "shrink_to_fit": True, "color": "#ffffff"},
    "pedido": {"left": 64, "top": 3.5, "text": "{{pedido}}", "font_size": 11, "bold": True, "width": 31,
               "align": "right", "shrink_to_fit": True, "color": "#ffffff"},
    "destinatario": {"left": 5, "top": 17, "text": "{{destinatario}}", "font_size": 17, "bold": True, "width": 90,
                     "max_lines": 2, "line_height": 1.05},
    "domicilio": {"left": 5, "top": 25.5, "text": "{{domicilio}}", "font_size": 11, "width": 90,
                  "shrink_to_fit": True},
    "localidad": {"left": 5, "top": 28.8, "text": "{{localidad}}", "font_size": 11, "width": 90,
                  "shrink_to_fit": True},
    "cp": {"left": 0, "top": 37.4, "text": "CP {{cp}}", "font_size": 32, "bold": True, "width": 100,
           "align": "center", "letter_spacing": 2, "color": "#ffffff"},
    "texts": [
        _caption(5, 14, "Enviar a", color="#475569"),
        {"left": 5, "top": 32, "text": "{{referencia}}", "font_size": 9.5, "width": 90, "hide_if_empty": True,
         "shrink_to_fit": True},
        {"left": 5, "top": 58, "text": "Envío: {{envio}}", "font_size": 9.5, "width": 90, "hide_if_empty": True,
         "shrink_to_fit": True},
        {"left": 5, "top": 88, "text": "De: {{remitente_domicilio}}", "font_size": 8.5, "width": 64,
         "hide_if_empty": True, "shrink_to_fit": True},
    ],
    "barcode": {"left": 5, "top": 66, "width": 6.4, "height": 2.2, "symbology": "code128", "data": "{{tracking}}",
                "show_text": True},
    "qr": {"left": 72, "top": 70, "size": 2.3, "data": "{{tracking_url}}"},
}

# --- E · Editorial ---------------------------------------------------------------
EDITORIAL = {
    "shapes": [
        _line("filete", 7, 9.5, width=86, stroke="#1c1917", stroke_width=0.8),
    ],
    "remitente": {"left": 7, "top": 4, "text": "{{remitente}}", "font_size": 15, "font_family": "serif",
                  "width": 58, "shrink_to_fit": True},
    "pedido": {"left": 60, "top": 4.8, "text": "{{pedido}}", "font_size": 9, "width": 33, "align": "right",
               "shrink_to_fit": True},
    "destinatario": {"left": 7, "top": 16, "text": "{{destinatario}}", "font_size": 22, "font_family": "serif",
                     "width": 86, "max_lines": 2, "line_height": 1.05},
    "domicilio": {"left": 7, "top": 27.5, "text": "{{domicilio}}", "font_size": 10.5, "width": 86,
                  "shrink_to_fit": True},
    "localidad": {"left": 7, "top": 30.8, "text": "{{localidad}}", "font_size": 10.5, "width": 86,
                  "shrink_to_fit": True},
    "cp": {"left": 7, "top": 38.5, "text": "{{cp}}", "font_size": 40, "font_family": "serif", "width": 60},
    "texts": [
        _caption(7, 13, "Destinatario", color="#78716c", bold=False, letter_spacing=2),
        {"left": 7, "top": 34.1, "text": "{{referencia}}", "font_size": 9.5, "width": 86, "hide_if_empty": True,
         "shrink_to_fit": True, "color": "#57534e"},
        _caption(7, 51.5, "Remitente", color="#78716c", bold=False, letter_spacing=2),
        {"left": 7, "top": 54.5, "text": "{{remitente_domicilio}}", "font_size": 9.5, "width": 86,
         "hide_if_empty": True, "shrink_to_fit": True},
        {"left": 7, "top": 57.7, "text": "Tel. {{remitente_telefono}}", "font_size": 9.5, "width": 86,
         "hide_if_empty": True},
        _caption(7, 62.5, "Envío", color="#78716c", bold=False, letter_spacing=2),
        {"left": 7, "top": 65.5, "text": "{{envio}}", "font_size": 9.5, "width": 86, "hide_if_empty": True,
         "shrink_to_fit": True},
    ],
    "barcode": {"left": 7, "top": 74, "width": 6.0, "height": 1.9, "symbology": "code128", "data": "{{tracking}}",
                "show_text": True},
    "qr": {"left": 70, "top": 74, "size": 2.2, "data": "{{tracking_url}}"},
}

# --- F · Banda lateral -------------------------------------------------------------
SIDE_BAND = {
    "palette": {"primary": "#7c2d12", "accent": "#9a3412"},
    "shapes": [
        _rect("banda", 0, 0, 18, 100, fill="primary", z=1),
        _rect("recuadro_cp", 23, 29, 30, 8, stroke="primary", stroke_width=2, radius=2.5, z=2),
        {"id": "arriba", "kind": "icon", "icon": "arriba", "left": 23, "top": 41, "width": 9, "height": 6,
         "fill": "#1c1917", "z": 3},
        {"id": "no_mojar", "kind": "icon", "icon": "no_mojar", "left": 34, "top": 41, "width": 9, "height": 6,
         "fill": "#1c1917", "z": 3},
        {"id": "fragil", "kind": "icon", "icon": "fragil", "left": 45, "top": 41, "width": 9, "height": 6,
         "fill": "#1c1917", "z": 3},
    ],
    "remitente": {"left": 6.5, "top": 6, "text": "{{remitente}}", "font_size": 15, "bold": True, "uppercase": True,
                  "rotate": 270, "width": 88, "shrink_to_fit": True, "color": "#fff7ed"},
    "destinatario": {"left": 23, "top": 9, "text": "{{destinatario}}", "font_size": 16, "bold": True,
                     "width": 72, "max_lines": 2, "line_height": 1.05},
    "domicilio": {"left": 23, "top": 17.8, "text": "{{domicilio}}", "font_size": 10, "width": 72,
                  "shrink_to_fit": True},
    "localidad": {"left": 23, "top": 21, "text": "{{localidad}}", "font_size": 10, "width": 72,
                  "shrink_to_fit": True},
    "cp": {"left": 23, "top": 30.4, "text": "{{cp}}", "font_size": 26, "bold": True, "width": 30, "align": "center",
           "color": "primary"},
    "pedido": {"left": 23, "top": 51, "text": "{{pedido}}", "font_size": 11, "bold": True, "width": 72},
    "texts": [
        _caption(23, 6, "Enviar a", color="accent"),
        {"left": 23, "top": 24.2, "text": "{{referencia}}", "font_size": 9, "width": 72, "hide_if_empty": True,
         "shrink_to_fit": True},
        {"left": 56, "top": 32, "text": "Código postal", "font_size": 8, "color": "#57534e"},
        {"left": 23, "top": 55, "text": "Envío: {{envio}}", "font_size": 9, "width": 72, "hide_if_empty": True,
         "shrink_to_fit": True},
        {"left": 23, "top": 58.5, "text": "De: {{remitente_domicilio}}", "font_size": 8.5, "width": 72,
         "hide_if_empty": True, "shrink_to_fit": True},
    ],
    "barcode": {"left": 23, "top": 65, "width": 5.8, "height": 2.0, "symbology": "code128", "data": "{{tracking}}",
                "show_text": True},
    "qr": {"left": 76, "top": 84, "size": 2.0, "data": "{{tracking_url}}"},
}

# --- G · Postal ------------------------------------------------------------------------
POSTAL = {
    "palette": {"primary": "#b45309", "accent": "#92400e"},
    "shapes": [
        _rect("fondo", 0, 0, 100, 100, fill="#fffbeb", z=1),
        {"id": "marco", "kind": "rect", "left": 3, "top": 2, "width": 94, "height": 96, "fill": None,
         "stroke": "primary", "stroke_width": 1.2, "stroke_style": "dashed", "radius": 4, "z": 2},
        {"id": "estampilla", "kind": "rect", "left": 71, "top": 5, "width": 21, "height": 17, "fill": "#fef3c7",
         "stroke": "primary", "stroke_width": 1.5, "stroke_style": "dotted", "radius": 1, "z": 3},
        {"id": "logo", "kind": "image", "image": "logo", "left": 74, "top": 6.5, "width": 15, "height": 10,
         "fit": "contain", "z": 4},
        {"id": "corazon", "kind": "icon", "icon": "corazon", "left": 78.5, "top": 17, "width": 6, "height": 4,
         "fill": "primary", "z": 4},
        _line("renglon1", 8, 36.6, width=84, stroke="#fcd34d"),
        _line("renglon2", 8, 41.6, width=84, stroke="#fcd34d"),
        _line("renglon3", 8, 46.6, width=84, stroke="#fcd34d"),
    ],
    "destinatario": {"left": 8, "top": 25, "text": "{{destinatario}}", "font_size": 15, "bold": True, "width": 84,
                     "max_lines": 2, "line_height": 1.05},
    "domicilio": {"left": 8, "top": 33.4, "text": "{{domicilio}}", "font_size": 11, "width": 84,
                  "shrink_to_fit": True},
    "localidad": {"left": 8, "top": 38.4, "text": "{{localidad}}", "font_size": 11, "width": 84,
                  "shrink_to_fit": True},
    "cp": {"left": 8, "top": 50, "text": "CP {{cp}}", "font_size": 22, "bold": True, "width": 60,
           "color": "accent"},
    "pedido": {"left": 8, "top": 84.5, "text": "{{pedido}}", "font_size": 9, "bold": True, "width": 60},
    "remitente": {"left": 8, "top": 88, "text": "De {{remitente}}", "font_size": 9, "width": 62,
                  "shrink_to_fit": True},
    "texts": [
        {"left": 8, "top": 7, "text": "¡Hola, {{nombre}}!", "font_size": 22, "font_family": "script",
         "width": 60, "hide_if_empty": True, "shrink_to_fit": True, "color": "accent"},
        {"left": 8, "top": 43.4, "text": "{{referencia}}", "font_size": 9.5, "width": 84, "hide_if_empty": True,
         "shrink_to_fit": True},
    ],
    "barcode": {"left": 8, "top": 64, "width": 6.0, "height": 1.9, "symbology": "code128", "data": "{{tracking}}",
                "show_text": True},
    "qr": {"left": 72, "top": 66, "size": 2.0, "data": "{{tracking_url}}"},
}

# --- B · Celdas ---------------------------------------------------------------------------
_C0, _C1, _C2, _CW = 4, 34.67, 65.33, 30.67  # columnas de la grilla (x y ancho en %)
_FULL = _CW * 3
# El código de barras tiene su fila entera: en ZPL un Code128 de 19 dígitos
# sale más ancho que en el PDF (~6 cm), y su margen blanco (QUIET_ZONE_CM) no
# puede pisar los bordes de la celda.
CELLS = {
    "shapes": [
        _cell("logo_celda", _C0, 3, _CW, 7, None, header=True),
        {"id": "logo", "kind": "image", "image": "logo", "left": _C0 + 2, "top": 3.8, "width": _CW - 4,
         "height": 5.4, "fit": "contain", "z": 2},
        _cell("h_pedido", _C1, 3, _CW, 7, "Pedido", header=True),
        _cell("v_pedido", _C2, 3, _CW, 7, None),
        _cell("h_seguimiento", _C0, 10, _FULL, 4, "Seguimiento", header=True),
        _cell("v_seguimiento", _C0, 14, _FULL, 11, None),
        _cell("h_bultos", _C0, 25, _CW, 4.5, "Bultos", header=True),
        _cell("h_peso", _C1, 25, _CW, 4.5, "Peso", header=True),
        _cell("h_servicio", _C2, 25, _CW, 4.5, "Servicio", header=True),
        _cell("v_bultos", _C0, 29.5, _CW, 6, "{{bultos_pedido}}"),
        _cell("v_peso", _C1, 29.5, _CW, 6, "{{peso}}"),
        _cell("v_servicio", _C2, 29.5, _CW, 6, "{{envio}}", font_size=7.5, max_lines=2, line_height=1.0),
        _cell("h_destinatario", _C0, 35.5, _FULL, 4.5, "Destinatario", header=True),
        _cell("v_destinatario", _C0, 40, _FULL, 14, None),
        _cell("h_cp", _C0, 54, _CW, 10, "CP", header=True),
        _cell("v_cp", _C1, 54, _CW * 2, 10, None),
        _cell("h_remitente", _C0, 64, _FULL, 4.5, "Remitente", header=True),
        _cell("v_remitente", _C0, 68.5, _CW * 2, 14, None),
        _cell("v_qr", _C2, 68.5, _CW, 14, None),
        _cell("h_obs", _C0, 82.5, _FULL, 4.5, "Observaciones", header=True),
        _cell("v_obs", _C0, 87, _FULL, 10, "{{referencia}}", font_size=9, bold=False, align="left",
              valign="top", max_lines=2, hide_if_empty=True),
    ],
    "pedido": {"left": _C2 + 1, "top": 4.6, "text": "{{pedido}}", "font_size": 11, "bold": True, "width": _CW - 2,
               "align": "center", "shrink_to_fit": True},
    "destinatario": {"left": _C0 + 2, "top": 41.6, "text": "{{destinatario}}", "font_size": 14, "bold": True,
                     "width": 88, "shrink_to_fit": True},
    "domicilio": {"left": _C0 + 2, "top": 46.6, "text": "{{domicilio}}", "font_size": 10, "width": 88,
                  "shrink_to_fit": True},
    "localidad": {"left": _C0 + 2, "top": 49.9, "text": "{{localidad}}", "font_size": 10, "width": 88,
                  "shrink_to_fit": True},
    "cp": {"left": _C1, "top": 55.3, "text": "{{cp}}", "font_size": 32, "bold": True, "width": _CW * 2,
           "align": "center"},
    "remitente": {"left": _C0 + 2, "top": 70, "text": "{{remitente}}", "font_size": 11, "bold": True,
                  "width": _CW * 2 - 4, "shrink_to_fit": True},
    "texts": [
        {"left": _C0 + 2, "top": 74, "text": "{{remitente_domicilio}}", "font_size": 8.5, "width": _CW * 2 - 4,
         "hide_if_empty": True, "shrink_to_fit": True},
        {"left": _C0 + 2, "top": 77.2, "text": "Tel. {{remitente_telefono}}", "font_size": 8.5,
         "width": _CW * 2 - 4, "hide_if_empty": True},
    ],
    "barcode": {"left": 14, "top": 16.2, "width": 6.4, "height": 0.6, "symbology": "code128",
                "data": "{{tracking}}", "show_text": True},
    "qr": {"left": 72.5, "top": 70, "size": 1.6, "data": "{{tracking_url}}"},
}

GALLERY = [
    {"key": "moderno", "order": 1, "name": "Moderno", "design": MODERN,
     "description": "Barra oscura con el logo, el nombre grande en mayúsculas, secciones numeradas y un color de acento."},
    {"key": "marca", "order": 2, "name": "Con la marca", "design": BRAND,
     "description": "Franja de color con el logo en círculo, letra con serifa, borde redondeado y un mensaje propio."},
    {"key": "destacado", "order": 3, "name": "Destacado", "design": HIGHLIGHT,
     "description": "Banda de color arriba, el código postal enorme en una banda negra y sello de frágil."},
    {"key": "editorial", "order": 4, "name": "Editorial", "design": EDITORIAL,
     "description": "Blanco y negro, letra con serifa y mucho aire. Sale perfecto en impresora térmica."},
    {"key": "banda_lateral", "order": 5, "name": "Banda lateral", "design": SIDE_BAND,
     "description": "La tienda en una franja vertical, código postal en recuadro e íconos de manipulación."},
    {"key": "postal", "order": 6, "name": "Postal", "design": POSTAL,
     "description": "Como una carta: estampilla con el logo, saludo manuscrito con el nombre y renglones."},
    {"key": "celdas", "order": 7, "name": "Celdas", "design": CELLS,
     "description": "Grilla con encabezados grises, como los rótulos de los correos. Se lee rápido."},
]
