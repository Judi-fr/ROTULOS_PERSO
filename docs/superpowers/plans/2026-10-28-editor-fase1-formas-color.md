# Editor de rótulos — Fase 1: formas, cajas, color y capas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. TDD con superpowers:test-driven-development; el inspector del editor con design-taste-frontend-v1 (elegidas por el usuario).

**Goal:** que el diseño de un rótulo admita formas (rectángulo, elipse, línea horizontal o vertical), cajas con un texto o dato adentro, colores con una paleta del diseño y orden de capas, dibujados igual en el lienzo del editor, en el PDF y en ZPL.

**Architecture:** dos claves nuevas y opcionales en el JSON `design`: `palette` (`{"primary": "#rrggbb", "accent": "#rrggbb"}`) y `shapes` (lista). Validación en un módulo nuevo `design_shapes.py` (llamado desde `serializers.validate_design`); dibujo en `shapes_pdf.py` y `shapes_zpl.py`, llamados por `draw_label_page` y `render_label_zpl` justo después de las decoraciones; en el editor, un archivo `label_editor_shapes.js`. Un diseño sin esas claves dibuja exactamente lo mismo que hoy.

**Tech Stack:** Django 6 / DRF, ReportLab (canvas), Pillow (bitmaps para ZPL), JS clásico sin build.

**Spec:** `docs/superpowers/specs/2026-10-28-editor-rotulos-galeria-design.md` (puntos 1, 2, 3 y 7 de "Lo que el editor tiene que poder hacer", "Impresora térmica", "Colores del diseño", reglas para elementos nuevos). Fases siguientes, cada una con su propio plan: 2 tipografía/varios renglones/íconos; 3 imágenes y datos nuevos; 4 comodidades de edición y vista térmica; 5 las ocho plantillas y la galería.

## Global Constraints

- **Regla del espejo:** cada propiedad nueva se dibuja igual en el lienzo (`editor_rotulos.html` + `label_editor_shapes.js`), en el PDF y en ZPL; si cambia una constante en un lado, cambia en los tres.
- **Compatibilidad:** un `design` sin `shapes`/`palette` produce las mismas llamadas de dibujo en el PDF y el mismo ZPL que antes (tests de regresión de la tarea 3 y 4). Ninguna clave guardada se renombra.
- Posiciones y tamaños en **% del rótulo** (`left`, `top`, `width`, `height`, 0–100), como el resto del diseño; `radius` y `padding` en **mm** (0–20); `stroke_width` en **pt** (0–6). Colores: `"#rrggbb"`, `"primary"`, `"accent"` o `null` (sin color).
- Máximo **60** formas por diseño; `id` único dentro del diseño, `^[a-z0-9_-]{1,20}$`.
- Térmica: luminancia `L = 0.299 r + 0.587 g + 0.114 b`; `L < 96` → negro, `L > 245` → blanco/nada (era 200: ver ruling de la Task 4), en el medio trama de gris (Bayer 4×4). Texto: `L >= 160` → blanco (invertido), si no negro.
- Código en inglés, textos al usuario en español. **No hacer commits** (CLAUDE.md: los hace el usuario); donde la skill pide commit, el paso es "dejar el árbol listo y anotar el cambio para el mensaje".
- Nunca modificar un test existente para que pase.
- Correr `python manage.py test apps.labels` y `node frontend/tools/check_frontend.mjs` al cerrar cada tarea.

## Review Focus

1. **Diseños viejos idénticos** — un `design` guardado hoy (la "Etiqueta de envío estándar" sembrada en `labels/0010`) genera las mismas llamadas de dibujo y el mismo ZPL: test en las tareas 3 y 4.
2. **Formas que se salen del rótulo** (`left + width > 100`) — se recortan al borde, no rompen ni dibujan fuera de página: test en las tareas 3 y 4.
3. **Paleta incompleta** (`"accent"` usado sin `palette.accent`) — la validación lo rechaza con un mensaje en español; el render, si le llega igual, lo trata como sin color: tests en las tareas 1 y 3.
4. **Gris grande en térmica** — un relleno gris a todo el rótulo a 300 dpi no produce un ZPL desmesurado: test en la tarea 4 (< 400 000 caracteres).
5. **Caja con dato vacío** — `{{referencia}}` vacío con `hide_if_empty` oculta la caja entera (forma incluida): test en las tareas 3 y 4.

---

### Task 1: Esquema, colores y validación (`design_shapes.py`)

**Files:**
- Create: `backend/apps/labels/design_shapes.py`
- Modify: `backend/apps/labels/serializers.py` (`validate_design`: aceptar `shapes` y `palette`; una regla puede apuntar a `"shape:<id>"`)
- Test: `backend/apps/labels/tests/test_design_shapes.py`

**Interfaces:**
- Produces:
  - `SHAPE_KINDS = ("rect", "ellipse", "line")`, `STROKE_STYLES = ("solid", "dashed", "dotted")`, `VALIGNS = ("top", "middle", "bottom")`, `MAX_SHAPES = 60`.
  - `parse_hex(value: str) -> tuple[int, int, int]` (acepta `#rgb` y `#rrggbb`, ValueError si no).
  - `resolve_color(value, palette: dict | None) -> str | None` → `"#rrggbb"` o `None` (referencia a paleta faltante → `None`, nunca lanza).
  - `thermal_tone(hex_color: str) -> tuple[str, float]` → `("black", 0)`, `("white", 0)` o `("gray", level)` con `level` 0–1 = proporción de puntos negros.
  - `validate_palette(value) -> None` y `validate_shapes(value, palette) -> None` (lanzan `serializers.ValidationError` en español).
  - `shape_rule_target(shape_id) -> str` = `f"shape:{shape_id}"`.
- Forma de una figura (todas las claves salvo `id`/`kind`/`left`/`top` opcionales):
  `{"id", "kind", "left", "top", "width", "height", "fill", "stroke", "stroke_width", "stroke_style", "radius", "z", "locked", "text": {"value", "font_size", "bold", "align", "valign", "color", "padding", "hide_if_empty"}}`.
  `line` usa `width` (horizontal, `height` 0) o `height` (vertical, `width` 0); `text` solo en `rect`/`ellipse`.

- [ ] **Step 1: tests que fallan**

```python
class ShapeColorTests(SimpleTestCase):
    def test_resolve_palette_and_hex(self):
        palette = {"primary": "#1E3A8A"}
        self.assertEqual(resolve_color("primary", palette), "#1e3a8a")
        self.assertEqual(resolve_color("#abc", None), "#aabbcc")
        self.assertIsNone(resolve_color("accent", palette))
        self.assertIsNone(resolve_color(None, palette))

    def test_thermal_tone_thresholds(self):
        self.assertEqual(thermal_tone("#000000"), ("black", 0))
        self.assertEqual(thermal_tone("#ffffff"), ("white", 0))
        tone, level = thermal_tone("#9ca3af")
        self.assertEqual(tone, "gray")
        self.assertTrue(0 < level < 1)

class ShapeValidationTests(SimpleTestCase):
    def test_valid_design_with_shapes_passes(self):
        validate_design({"palette": {"primary": "#7c2d12"},
                         "shapes": [{"id": "band", "kind": "rect", "left": 0, "top": 0, "width": 18, "height": 100,
                                     "fill": "primary", "text": {"value": "LAS LILAS", "color": "#ffffff"}}]})

    def test_rejections(self):
        bad = [
            {"shapes": [{"id": "a", "kind": "star", "left": 0, "top": 0}]},
            {"shapes": [{"id": "a", "kind": "rect", "left": 0, "top": 0, "fill": "accent"}]},   # sin palette.accent
            {"shapes": [{"id": "a", "kind": "rect", "left": 0, "top": 0, "fill": "rojo"}]},
            {"shapes": [{"id": "a", "kind": "rect", "left": 0, "top": 0}, {"id": "a", "kind": "rect", "left": 1, "top": 1}]},
            {"shapes": [{"id": "a", "kind": "line", "left": 0, "top": 0, "width": 50, "height": 20}]},  # ni h ni v
            {"shapes": [{"id": "a", "kind": "rect", "left": 0, "top": 0, "radius": 25}]},
            {"shapes": [{"id": f"s{i}", "kind": "rect", "left": 0, "top": 0} for i in range(61)]},
        ]
        for design in bad:
            with self.assertRaises(serializers.ValidationError):
                validate_design(design)

    def test_rule_can_target_shape(self):
        validate_design({"shapes": [{"id": "fragil", "kind": "rect", "left": 0, "top": 0, "width": 10, "height": 5}],
                         "rules": [{"when": {"field": "envio", "op": "contains", "value": "frágil"},
                                    "then": {"action": "hide", "target": "shape:fragil"}}]})
```

- [ ] **Step 2:** `python manage.py test apps.labels.tests.test_design_shapes` → FAIL (módulo inexistente).
- [ ] **Step 3:** implementar `design_shapes.py` con las firmas de arriba; en `serializers.validate_design`, tratar `palette` y `shapes` como `DECORATION_KEYS` (se validan con las funciones nuevas, no son campos posicionados) y sumar `shape:<id>` de cada forma a `known_targets` de las reglas.
- [ ] **Step 4:** el mismo comando → PASS; `python manage.py test apps.labels` → todo en verde.
- [ ] **Step 5:** sin commit; anotar "validación de formas y paleta".

### Task 2: Reglas sobre formas (`apply_design_rules`)

**Files:**
- Modify: `backend/apps/labels/label_rendering.py:605` (`apply_design_rules`)
- Test: `backend/apps/labels/tests/test_design_shapes.py`

**Interfaces:**
- Consumes: `shape_rule_target` (Task 1).
- Produces: el diseño efectivo trae `shapes` ya filtradas (las ocultas no están) y con `left`/`top`/`text.value` cambiados por `move`/`set_text`. `shapes_pdf`/`shapes_zpl` dibujan lo que reciben sin mirar reglas.

- [ ] **Step 1: test que falla**

```python
def test_rules_hide_move_and_set_text_on_shapes(self):
    design = {"shapes": [{"id": "sello", "kind": "rect", "left": 5, "top": 5, "width": 20, "height": 6,
                          "text": {"value": "FRÁGIL"}}],
              "rules": [
                  {"when": {"field": "envio", "op": "equals", "value": "A"}, "then": {"action": "hide", "target": "shape:sello"}},
                  {"when": {"field": "envio", "op": "equals", "value": "B"}, "then": {"action": "move", "target": "shape:sello", "left": 50, "top": 60}},
                  {"when": {"field": "envio", "op": "equals", "value": "C"}, "then": {"action": "set_text", "target": "shape:sello", "value": "URGENTE"}},
              ]}
    self.assertEqual(apply_design_rules(design, {"envio": "A"}, {})["shapes"], [])
    moved = apply_design_rules(design, {"envio": "B"}, {})["shapes"][0]
    self.assertEqual((moved["left"], moved["top"]), (50, 60))
    self.assertEqual(apply_design_rules(design, {"envio": "C"}, {})["shapes"][0]["text"]["value"], "URGENTE")
    self.assertEqual(design["shapes"][0]["left"], 5)   # el original no se toca
```

- [ ] **Step 2:** correr → FAIL. **Step 3:** en `apply_design_rules`, copiar `shapes` (copia profunda de cada forma y su `text`) e indexarlas por `shape:<id>` para que `target in effective` las encuentre; al final quitar las ocultas. **Step 4:** PASS + `apps.labels` en verde. **Step 5:** anotar.

### Task 3: Dibujo en PDF (`shapes_pdf.py`)

**Files:**
- Create: `backend/apps/labels/shapes_pdf.py`
- Modify: `backend/apps/labels/label_rendering.py:825` (`draw_label_page`: llamar `draw_shapes` después de `_draw_decorations`)
- Test: `backend/apps/labels/tests/test_shapes_pdf.py` (reusa el `RecordingCanvas` de `test_text_options.py`, extendido con `roundRect`, `ellipse`, `setFillColorRGB`, `setStrokeColorRGB`, `setLineWidth`, `setDash`, `drawCentredString`, `drawRightString`)

**Interfaces:**
- Consumes: `resolve_color`, `parse_hex` (Task 1); `_replace_placeholders`, `_truncate_to_width`, `FONT_NAME`, `BOLD_FONT_NAME`, `CM_TO_POINTS` de `label_rendering`.
- Produces: `draw_shapes(pdf, design, context, computed, width_cm, height_cm, default_font_size) -> None`. Orden: por `z` ascendente (empate: orden de la lista). Las formas quedan **debajo** de logo, códigos y textos de siempre (capa de fondo); el texto propio de una caja se dibuja con su caja.

- [ ] **Step 1: tests que fallan**
  - `test_rect_fill_stroke_radius_and_z`: dos rect con `z` 2 y 1 → se dibujan en orden z; la de `radius: 3` usa `roundRect` con radio `3 mm` en pt; relleno `#1e3a8a` → `setFillColorRGB(30/255, 58/255, 138/255)`.
  - `test_vertical_and_dotted_line`: `{"kind": "line", "left": 50, "top": 10, "height": 80, "stroke_style": "dotted"}` → una `line` vertical x = 50 % del ancho, con `setDash`.
  - `test_box_text_alignment`: caja `align: "center", valign: "middle"` → `drawCentredString` en el centro horizontal y la línea base centrada verticalmente; `color: "#ffffff"` → texto blanco.
  - `test_shape_clipped_to_label`: `left: 90, width: 30` → el rect dibujado termina en el borde derecho.
  - `test_hide_if_empty_box`: texto `{{referencia}}` vacío con `hide_if_empty` → no se dibuja ni la forma.
  - `test_missing_palette_color_draws_nothing`: `fill: "accent"` sin paleta → sin relleno, sin error.
  - `test_existing_standard_template_unchanged`: la `design` de la plantilla sembrada en `labels/0010` dibujada con el `RecordingCanvas` produce la misma lista de llamadas con y sin el código nuevo (fijar la lista esperada leyendo el resultado de hoy antes del cambio).
- [ ] **Step 2:** correr → FAIL.
- [ ] **Step 3:** implementar `draw_shapes` (ReportLab: `rect`/`roundRect`/`ellipse`/`line`, `setDash(1,2)` punteado, `setDash(4,3)` discontinuo; texto de caja recortado con `_truncate_to_width` al ancho de la caja menos `padding`), y llamarla en `draw_label_page`.
- [ ] **Step 4:** PASS + `apps.labels` en verde (incluye `test_scanning`: un código encima de una forma sigue leyéndose — el código ya pinta su caja blanca).
- [ ] **Step 5:** anotar.

### Task 4: Dibujo en ZPL (`shapes_zpl.py`)

**Files:**
- Create: `backend/apps/labels/shapes_zpl.py`
- Modify: `backend/apps/labels/zpl.py:590` (`render_label_zpl`: sumar `shape_commands` después de `_decoration_commands`)
- Test: `backend/apps/labels/tests/test_shapes_zpl.py`

**Interfaces:**
- Consumes: `resolve_color`, `thermal_tone` (Task 1); `cm_to_dots`, `points_to_dots`, `escape_field_data`, `_pack_monochrome`, `_dashed_segment` de `zpl.py`; `_replace_placeholders`.
- Produces: `shape_commands(design, context, computed, width_cm, height_cm, default_font_pt, dpmm) -> list[str]`, mismo orden por `z` que el PDF.
- Reglas de traducción (decididas acá): rect/línea **negra sólida** → `^GB` (relleno = grosor igual al lado menor; esquinas con el 5.º parámetro de `^GB`, `round(8 * radius_dots / (min(w,h)/2))` acotado 0–8); elipse negra → `^GE`; **cualquier gris, punteado sobre esquinas redondeadas o elipse con borde de otro grosor** → se rasteriza con Pillow a 1 bit (Bayer 4×4 según `level`) y va como `^GF`; blanco → nada. Texto de caja: `^FB` con la alineación; color claro (`L >= 160`) → `^FR`.

- [ ] **Step 1: tests que fallan**
  - `test_black_rounded_rect_uses_gb_with_rounding`: rect negro con `radius` → una línea `^GB...,B,<r>` con `0 < r <= 8`.
  - `test_vertical_line`: línea vertical → `^GB0,<largo>,<grosor>`.
  - `test_gray_fill_becomes_graphic`: relleno `#9ca3af` → hay un `^GF`; ningún `^GB` lleno para esa forma.
  - `test_light_text_on_dark_box_is_reversed`: caja negra con texto `#ffffff` → el campo de texto lleva `^FR`.
  - `test_full_label_gray_is_bounded`: rect gris 0/0/100/100 a `dpmm=12` → `len(zpl) < 400_000`.
  - `test_hide_if_empty_box` y `test_shape_clipped_to_label` como en el PDF.
  - `test_existing_standard_template_zpl_unchanged`: el ZPL de la plantilla sembrada es idéntico al de hoy (fijar el texto esperado antes del cambio).
  - Con `ZPL_LABELARY_TESTS=1`: renderizar un diseño con las tres formas en Labelary a 8 y 12 dpmm y que responda 200 (mismo patrón que `test_zpl.LabelaryRenderTests`).
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS + `apps.labels` en verde. **Step 5:** anotar.

### Task 5: Editor — dibujar, elegir y mover formas (`label_editor_shapes.js`)

**Files:**
- Create: `frontend/assets/js/label_editor_shapes.js` (expone `window.LabelEditorShapes`)
- Modify: `frontend/editor_rotulos.html` (cargar el script después de `label_editor_snap.js`; una capa `#shapeLayer` debajo de los campos y encima de `#decorationLayer`; `collectFields()` suma `shapes` y `palette` cuando hay; al abrir una plantilla, `LabelEditorShapes.load(design)`)
- Test: verificación en el navegador (no hay tests de JS en el proyecto) + `node frontend/tools/check_frontend.mjs`

**Interfaces:**
- Produces: `window.LabelEditorShapes = { load(design), collect() -> {shapes, palette}, render(perCm, widthCm, heightCm, defaultFontPt), add(kind, options), select(id), selected() }`. `render` usa los mismos números que el PDF: grosor `stroke_width` pt → px con el `ptToPx` del editor, `radius`/`padding` mm → px con `perCm / 10`, punteado `1 2`/discontinuo `4 3` vía `border-style` o SVG.
- Comportamiento: clic elige (anillo de selección), arrastre mueve (con el imán de `label_editor_snap.js`, que suma los bordes de las formas como guías), tiradores en las esquinas cambian `width`/`height`; flechas mueven 0,5 % (Shift 5 %); bloqueo igual que los campos (`locked`). Las formas usan coordenadas en % guardadas en el estado, como las decoraciones (el DOM se regenera en cada render).

- [ ] **Step 1:** con una plantilla que ya traiga `shapes` (crearla por la API en la base local con un rect de fondo, una línea vertical y una caja con `{{cp}}`), comprobar en el navegador que hoy NO se ven (falla esperada).
- [ ] **Step 2:** implementar `load`/`render`/`collect` y la selección/arrastre/redimensión.
- [ ] **Step 3:** en el navegador: las tres formas se ven donde el PDF de "Ver cómo sale impreso" las pone (comparar visualmente); moverlas y guardar conserva la posición; abrir y guardar sin tocar devuelve el mismo `design` (comparar el JSON antes/después con `fetch`).
- [ ] **Step 4:** `check_frontend.mjs` → "Sin problemas."; consola sin errores.

### Task 6: Editor — panel de la forma, paleta, capas y alta de formas

**Files:**
- Modify: `frontend/editor_rotulos.html` (panel "Formas" en el inspector: botones **+ Rectángulo**, **+ Círculo**, **+ Línea horizontal**, **+ Línea vertical**, **+ Caja con texto**; panel "Forma seleccionada"; panel "Colores del diseño")
- Modify: `frontend/assets/js/label_editor_shapes.js`
- Modify: `frontend/assets/js/label_editor_markers.js` (el selector "Insertar dato" también en el texto de una caja)
- Test: navegador + `check_frontend.mjs`

**Interfaces:**
- Consumes: `LabelEditorShapes` (Task 5); el selector "Insertar dato" que hoy arma `label_editor_markers.js` para campos, textos fijos y códigos (reusarlo, no copiarlo: si hace falta, exponerlo en `window.LabelEditorMarkers`).
- Panel "Forma seleccionada" (agrupado y progresivo, design-taste-frontend-v1: lo que no aplica a la forma no se muestra): *Relleno* (paleta / color libre / sin relleno), *Borde* (color, grosor, estilo), *Esquinas* (solo rect), *Texto* (solo cajas: contenido con "Insertar dato", tamaño, negrita, alineación horizontal y vertical, color, margen, ocultar si queda vacío), *Capa* (**Traer al frente** / **Enviar atrás** = `z` máximo + 1 / mínimo − 1), *Bloquear*, *Eliminar*.
- "Colores del diseño": dos muestras (principal y acento) que recolorean todo lo que las usa; los selectores de color ofrecen "Principal", "Acento", 8 colores fijos y "Otro…" (`<input type="color">`).
- Aviso fijo cuando el diseño usa algún color que no es negro/blanco: "En impresora térmica los colores salen en negro o gris."

- [ ] **Step 1:** en el navegador, construir desde cero la franja y el CP de **F** (banda lateral `primary` a todo el alto con "LAS LILAS" rotado… el rotado de texto de caja queda para la fase 2: acá la banda y la caja del CP con borde redondeado) y la barra superior de **H** (rect negro a todo el ancho + caja con `{{pedido}}` en blanco). Hoy no se puede (falla esperada).
- [ ] **Step 2:** implementar los paneles.
- [ ] **Step 3:** repetir el Step 1 en el navegador: se puede, y "Ver cómo sale impreso" coincide con el lienzo; cambiar "Principal" recolorea la banda; "Traer al frente"/"Enviar atrás" cambian el orden en lienzo y PDF; guardar la plantilla y reabrirla conserva todo.
- [ ] **Step 4:** generar el ZPL de esa plantilla (`POST /labels/render/` con `format: "zpl"`) y revisar que tenga los `^GB`/`^GF` esperados.

### Task 7: Cierre de la fase

**Files:**
- Modify: `CLAUDE.md` (sección Labels → System 1: claves `shapes`/`palette`, reglas de térmica, archivos nuevos y la regla de que las formas van debajo de logo/códigos/textos)
- Test: suite completa

- [ ] **Step 1:** `python manage.py test` (completa, `--parallel 2`) y `flake8` → verde.
- [ ] **Step 2:** miniaturas: guardar una plantilla con formas y comprobar que su miniatura en `mis_plantillas.html` las muestra (sale del mismo PDF, `thumbnails.py`).
- [ ] **Step 3:** documentar en CLAUDE.md y armar el texto del mensaje de commit para el usuario.
