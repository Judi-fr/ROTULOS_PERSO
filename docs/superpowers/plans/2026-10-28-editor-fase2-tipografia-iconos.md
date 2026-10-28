# Editor de rótulos — Fase 2: tipografía, varios renglones e íconos — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (elegido por el usuario) to implement this plan task-by-task, con superpowers:test-driven-development; el panel del editor con design-taste-frontend-v1. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que cualquier texto del rótulo (campos, textos fijos y texto de cajas) pueda llevar tipografía (sans, serif, manuscrita, condensada, máquina de escribir), itálica, mayúsculas, espaciado entre letras y varios renglones con interlineado, y que el diseño admita íconos de un set fijo — igual en el lienzo, el PDF y el ZPL.

**Architecture:** las tipografías viven como `.ttf` en el repo (backend y una copia idéntica en el frontend, verificada por test). Un módulo nuevo `text_style.py` concentra registro de fuentes, medición, corte en renglones y dibujo de una línea (PDF) y el texto como gráfico (ZPL); `label_rendering._draw_text_entry`, `shapes_pdf`, `zpl._text_commands` y `shapes_zpl` lo usan. Los íconos son una forma más (`kind: "icon"`) de la fase 1, con su geometría en un JSON compartido (`icons.json`). En el editor, un módulo `label_editor_type.js` arma los controles de texto una sola vez y los reusan los tres paneles.

**Tech Stack:** ReportLab (TTFont, `setCharSpace`), Pillow (`ImageFont.truetype`), JS sin build, `@font-face`.

**Spec:** `docs/superpowers/specs/2026-10-28-editor-rotulos-galeria-design.md` (punto 4 "Tipografía" y punto 5 "Íconos"). Fase 1 hecha: `docs/superpowers/plans/2026-10-28-editor-fase1-formas-color.md` (formas, `design_shapes.py`, `shapes_pdf.py`, `shapes_zpl.py`, `label_editor_shapes.js`).

## Global Constraints

- **Regla del espejo** (CLAUDE.md): lienzo, PDF y ZPL iguales; el PDF tiene la última palabra.
- **Compatibilidad:** un diseño sin las claves nuevas imprime idéntico: los tests `test_existing_standard_template_unchanged` (PDF y ZPL, fase 1) siguen pasando sin tocarlos.
- **Tipografías (aprobadas por el usuario, OFL, de github.com/google/fonts):** serif = Libre Baskerville (Regular, Italic, Bold); manuscrita = Kalam (Regular, Bold); condensada = PT Sans Narrow (Regular, Bold); máquina de escribir = Courier Prime (Regular, Italic, Bold, BoldItalic). Sans = Helvetica de ReportLab (la de hoy); para dibujarla como gráfico en ZPL, Liberation Sans (OFL, copiada de `/usr/share/fonts/truetype/liberation/`, métricamente igual). Cada familia con su `OFL.txt`.
- Claves de estilo nuevas (en campos de texto, `texts[]` y `shapes[].text`): `font_family` ∈ `sans|serif|script|condensed|mono` (default `sans`), `italic` (bool), `uppercase` (bool), `letter_spacing` (pt, −1 a 10, default 0), `max_lines` (1–10, default 1), `line_height` (0,8–3, múltiplo del tamaño, default 1,15), y en campos y `texts[]` también `color` (como en la fase 1: `#rrggbb`, `primary`, `accent`; en térmica, claro ≥ 160 de luminancia → `^FR`, si no negro — necesario para los títulos grises del diseño E). Una combinación que la familia no tiene (Kalam itálica, PT Sans Narrow itálica, Libre Baskerville negrita itálica) cae a la variante más cercana sin error.
- Texto rotado (`rotate` 90/270) siempre en un renglón (`max_lines` se ignora); sin cambios respecto de hoy.
- ZPL: `^A0` nativo solo para `sans` sin itálica ni espaciado (varios renglones con `^FB` y su interlineado); cualquier otra cosa se dibuja con Pillow y la misma `.ttf` como `^GF` comprimido (`shapes_zpl.compress_rows`), con `^FR` si va invertido.
- Íconos: `shapes[]` con `kind: "icon"`, `icon` ∈ `fragil|arriba|no_mojar|no_apilar|telefono|casa|corazon|estrella|camion|caja`, color en `fill` (default negro); se dibuja centrado y "contenido" en su caja (sin deformarse). Geometría en una grilla de 24×24: lista de primitivas `{"poly": [[x,y],...], "closed": bool, "fill": bool, "width": n}` y `{"circle": [cx,cy,r], "fill": bool, "width": n}`.
- Código en inglés, textos al usuario en español. **Sin commits** (los hace el usuario). Nunca tocar un test existente para que pase.
- Al cerrar cada tarea: `python manage.py test apps.labels` y `node frontend/tools/check_frontend.mjs`.

## Review Focus

1. **Acentos y ñ en cada tipografía** ("Martínez", "Peña", "¡Hola!") — se dibujan en PDF y ZPL sin cuadrados: test en las tareas 1 y 4.
2. **Una palabra más larga que la caja** con varios renglones — se corta con elipsis, no se sale ni cuelga el render: test en la tarea 3.
3. **Combinación que la familia no tiene** (manuscrita + itálica) — imprime con la variante cercana, sin 500: test en la tarea 1.
4. **Texto invertido con tipografía no nativa en ZPL** — sale blanco sobre la franja negra (`^FR` sobre el `^GF`): test en la tarea 4.
5. **Ícono en una caja no cuadrada** — no se deforma, queda centrado: test en la tarea 5.

---

### Task 1: Tipografías en el repo y `text_style.py`

**Files:**
- Create: `backend/apps/labels/fonts/` (los `.ttf` + `OFL-<familia>.txt`), `frontend/assets/fonts/` (copia idéntica de los mismos `.ttf`)
- Create: `backend/apps/labels/text_style.py`
- Test: `backend/apps/labels/tests/test_text_style.py`

**Interfaces:**
- Produces:
  - `FONT_FAMILIES = ("sans", "serif", "script", "condensed", "mono")`.
  - `FONT_FILES: dict[str, dict[str, str]]` — por familia, variante (`regular|bold|italic|bolditalic`) → nombre de archivo en `fonts/`.
  - `pdf_font_name(family: str, bold: bool, italic: bool) -> str` — registra (una vez) y devuelve el nombre de fuente de ReportLab; `sans` → `Helvetica`/`Helvetica-Bold`/`Helvetica-Oblique`/`Helvetica-BoldOblique`; familia desconocida → sans; variante faltante → la más cercana (bolditalic→bold→italic→regular).
  - `ttf_path(family, bold, italic) -> pathlib.Path` — el archivo para Pillow (sans → Liberation Sans).

- [ ] **Step 1: bajar las fuentes** (aprobado por el usuario): de `https://github.com/google/fonts/raw/main/ofl/<familia>/` los 11 `.ttf` de Global Constraints y sus `OFL.txt`; copiar `LiberationSans-{Regular,Bold,Italic,BoldItalic}.ttf` y su licencia del sistema. Comprobar con `fc-scan` que cada archivo es TrueType y trae `áéíóúñ¡¿`.
- [ ] **Step 2: tests que fallan**
  - `test_every_family_variant_resolves`: para cada familia y las 4 combinaciones de bold/italic, `pdf_font_name` devuelve un nombre registrado en `pdfmetrics` y `ttf_path` existe.
  - `test_missing_variant_falls_back`: `pdf_font_name("script", False, True)` == `pdf_font_name("script", False, False)`; `pdf_font_name("serif", True, True)` == `pdf_font_name("serif", True, False)`.
  - `test_unknown_family_is_sans`: `pdf_font_name("comic", False, False) == "Helvetica"`.
  - `test_accents_have_glyphs`: para cada `.ttf`, `TTFont(...).face.charToGlyph` tiene `ord(c)` para `c in "áéíóúÁÉÍÓÚñÑ¡¿°"`.
  - `test_frontend_copies_are_identical`: sha256 de cada `.ttf` de `backend/apps/labels/fonts/` igual al de `frontend/assets/fonts/` (salvo Liberation, que solo usa el backend).
- [ ] **Step 3:** FAIL. **Step 4:** implementar `text_style.py` (registro con `pdfmetrics.registerFont(TTFont(...))`, nombres `RotulosSerif`, `RotulosSerif-Bold`, …). **Step 5:** PASS + `apps.labels` en verde. Sin commit; anotar en el ledger.

### Task 2: Validación de las claves de estilo nuevas

**Files:**
- Modify: `backend/apps/labels/serializers.py` (`_validate_text_style`), `backend/apps/labels/design_shapes.py` (`_validate_shape_text`)
- Test: `backend/apps/labels/tests/test_text_style.py`

**Interfaces:**
- Consumes: `FONT_FAMILIES` (Task 1).
- Produces: `text_style.validate_text_typography(entry: dict, label: str) -> None` (lanza `serializers.ValidationError` en español), llamado desde los dos validadores.

- [ ] **Step 1: tests que fallan** — `test_typography_keys_accepted` (un campo, un `texts[]` y una caja con `font_family: "serif", italic: True, uppercase: True, letter_spacing: 1.5, max_lines: 2, line_height: 1.2` pasan `validate_design`); `test_typography_rejections` (`font_family: "comic"`, `letter_spacing: 11`, `max_lines: 0`, `max_lines: 11`, `line_height: 0.5`, `italic: "sí"`, `color: "gris"`, `color: "accent"` sin paleta → `ValidationError`).
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS + `apps.labels`. Anotar.

### Task 3: PDF — tipografía y varios renglones

**Files:**
- Modify: `backend/apps/labels/text_style.py`, `backend/apps/labels/label_rendering.py` (`_draw_text_entry`), `backend/apps/labels/shapes_pdf.py` (`_draw_box_text`)
- Test: `backend/apps/labels/tests/test_text_style_pdf.py` (reusa el `RecordingCanvas` de `test_shapes_pdf.py`, sumando `setCharSpace` y `beginText`/`drawText` si se usan)

**Interfaces:**
- Consumes: `pdf_font_name` (Task 1).
- Produces:
  - `text_width(text, font_name, size, letter_spacing) -> float` (pt; `stringWidth` + `letter_spacing × (len−1)`).
  - `wrap_lines(text, font_name, size, max_width_pt, max_lines, letter_spacing) -> list[str]` — corte por palabras; una palabra que no entra se corta por caracteres; si sobra texto, el último renglón termina en "…".
  - `draw_text_line(pdf, x, y, text, font_name, size, align, letter_spacing)` — `align` respecto de `x` como hoy (`left` desde x, `center` centrado en x, `right` termina en x); con espaciado usa un `textobject` con `setCharSpace`.
  - `resolve_text_style(entry) -> dict` — `{font_name, size, italic, uppercase, letter_spacing, max_lines, line_height}` con los defaults; `uppercase` aplicado al texto ya resuelto (después de los marcadores).
  - Renglones: el primero en la misma línea base que hoy; los siguientes bajan `size × line_height`. En cajas, el bloque entero se alinea según `valign`.

- [ ] **Step 1: tests que fallan**
  - `test_serif_italic_uppercase_field`: campo `{{destinatario}}` con `font_family: "serif", italic: True, uppercase: True` → un texto "MARIA GÓMEZ" con la fuente `RotulosSerif-Italic`.
  - `test_two_lines_wrap`: `destinatario` "Mariela Quiroga de los Santos" con `max_lines: 2, width: 30` en 10×15 → dos llamadas de texto, la segunda `size × 1.15` más abajo.
  - `test_long_word_is_cut_with_ellipsis`: una palabra de 80 letras con `max_lines: 2` → dos renglones, el último termina en "…", ninguno más ancho que la caja.
  - `test_field_color_from_palette`: campo con `color: "primary"` y `palette.primary: "#7c2d12"` → el texto sale con ese color.
  - `test_letter_spacing_uses_char_space`: `letter_spacing: 2` → el canvas recibe `setCharSpace(2)`.
  - `test_box_text_two_lines_middle`: caja con `max_lines: 2` y `valign: "middle"` → el bloque de dos renglones centrado en la caja.
  - `test_existing_standard_template_unchanged` (fase 1) sigue en verde.
- [ ] **Step 2:** FAIL. **Step 3:** implementar y usar en `_draw_text_entry` (horizontal; rotado sigue en un renglón) y `_draw_box_text`. **Step 4:** PASS + `apps.labels` (incluye escaneo). Anotar.

### Task 4: ZPL — tipografía nativa o como gráfico

**Files:**
- Modify: `backend/apps/labels/text_style.py`, `backend/apps/labels/zpl.py` (`_text_commands`), `backend/apps/labels/shapes_zpl.py` (`_box_text`)
- Test: `backend/apps/labels/tests/test_text_style_zpl.py`

**Interfaces:**
- Consumes: `ttf_path`, `wrap_lines`, `resolve_text_style` (Tasks 1 y 3); `compress_rows` de `shapes_zpl`.
- Produces: `zpl_text_graphic(lines, ttf, size_dots, box_width_dots, align, line_height, letter_spacing_dots, reverse) -> list[str]` — dibuja los renglones con Pillow (`ImageFont.truetype`, 1 bit) y devuelve `["^FO…", ("^FR" si reverse), "^GFA,…^FS"]`. El corte en renglones usa las métricas de la misma `.ttf` en puntos (igual que el PDF).

- [ ] **Step 1: tests que fallan**
  - `test_plain_sans_stays_native`: campo sin claves nuevas → mismo ZPL de hoy (`^A0`).
  - `test_sans_two_lines_native`: `max_lines: 2` en sans → `^FB<ancho>,2,<interlineado>,L,0`.
  - `test_serif_becomes_graphic`: `font_family: "serif"` → hay `^GFA` y no hay `^A0` para ese campo.
  - `test_inverted_script_is_reversed_graphic`: `font_family: "script", invert: True` → franja `^GB` + `^FR` antes del `^GFA`.
  - `test_light_field_color_is_reversed`: campo sans con `color: "#ffffff"` → `^FR` en el campo.
  - `test_accents_render_in_graphic`: el gráfico de "Peña ¡Hola!" en `script` tiene tinta (no está vacío) y su ancho coincide ±2 puntos con `text_width` del PDF convertido a dots.
  - `test_existing_standard_template_zpl_unchanged` (fase 1) sigue en verde.
  - Con `ZPL_LABELARY_TESTS=1`: un diseño con las cinco familias, 2 renglones e invertido responde 200 en Labelary a 8 y 12 dpmm.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** PASS + `apps.labels`; mirar el PNG de Labelary. Anotar.

### Task 5: Íconos (forma `kind: "icon"`)

**Files:**
- Create: `backend/apps/labels/icons.json` (10 íconos, grilla 24×24, primitivas de Global Constraints), copia idéntica en `frontend/assets/data/label_icons.json`
- Create: `backend/apps/labels/icons.py`
- Modify: `backend/apps/labels/design_shapes.py` (`SHAPE_KINDS` suma `icon`; `icon` obligatorio y conocido; sin `text`), `shapes_pdf.py`, `shapes_zpl.py`
- Test: `backend/apps/labels/tests/test_icons.py`

**Interfaces:**
- Produces: `ICON_NAMES`; `icon_box(left_pt, top_pt, width_pt, height_pt) -> (x, y, side)` (cuadrado contenido y centrado); `draw_icon_pdf(pdf, name, x, y_top, side, color)`; `icon_bitmap(name, side_dots) -> PIL.Image` (1 bit, para `^GF`).
- Calidad: cada ícono legible a 1 cm (se mira en el PDF y en Labelary a 203 dpi).

- [ ] **Step 1: tests que fallan** — `test_icon_validation` (nombre desconocido o sin `icon` → error; con `text` → error); `test_icon_pdf_is_square_and_centered` (caja 40 %×10 % → el dibujo ocupa un cuadrado del lado menor, centrado); `test_icon_color_uses_fill`; `test_icon_zpl_is_graphic` (`^GFA` con tinta); `test_every_icon_draws` (los 10 en PDF y ZPL sin error); `test_icons_json_copies_identical`.
- [ ] **Step 2:** FAIL. **Step 3:** dibujar los 10 íconos e implementar. **Step 4:** PASS + `apps.labels`; mirar PDF y Labelary. Anotar.

### Task 6: Editor — tipografías y controles de texto

**Files:**
- Create: `frontend/assets/js/label_editor_type.js` (`window.LabelEditorType`)
- Modify: `frontend/editor_rotulos.html` (`@font-face` de `assets/fonts/`; el lienzo aplica las claves nuevas en campos —`applyRenderMetrics`—, textos fijos —`renderDecorations`— y los paneles `fieldPropsGroup` y `decoPropsGroup`), `frontend/assets/js/label_editor_shapes.js` (texto de cajas y su panel)
- Test: navegador + `check_frontend.mjs`

**Interfaces:**
- Produces: `LabelEditorType.FAMILIES` (`[["sans","Sans (Helvetica)"],["serif","Serif"],["script","Manuscrita"],["condensed","Condensada"],["mono","Máquina de escribir"]]`), `LabelEditorType.cssFamily(family)`, `LabelEditorType.applyToElement(el, style, perCm, defaultPt)` (familia, itálica, mayúsculas, espaciado px, `-webkit-line-clamp` con `max_lines`, `line-height`), `LabelEditorType.renderControls(container, getStyle, setKey)` — un bloque "Tipografía" (familia, itálica, mayúsculas, espaciado, renglones, interlineado) que se inserta en los tres paneles; borra la clave cuando vuelve al default (como el panel de campos de hoy).
- La itálica se deshabilita para `script` y `condensed` (no la tienen) con un `title` que lo explica.

- [ ] **Step 1:** en el navegador, poner `font_family: "serif"` a un campo por la API y comprobar que hoy el lienzo no cambia (falla esperada).
- [ ] **Step 2:** implementar.
- [ ] **Step 3:** en el navegador, con los controles: el nombre del destinatario en serif, mayúsculas, espaciado 1 pt y 2 renglones; un texto fijo "¡Hola, Mariela!" en manuscrita; una caja con texto condensado. "Ver cómo sale impreso" coincide con el lienzo; guardar y reabrir conserva todo; consola sin errores.

### Task 7: Editor — íconos

**Files:**
- Modify: `frontend/assets/js/label_editor_shapes.js` (`kind: "icon"`: dibujo SVG desde `assets/data/label_icons.json`, cargado una vez; preset `+ Ícono`; en el panel, la grilla de los 10 íconos para cambiarlo y el color en "Relleno")
- Modify: `frontend/editor_rotulos.html` (botón `+ Ícono` en "Formas y colores")
- Test: navegador + `check_frontend.mjs`

- [ ] **Step 1:** implementar. **Step 2:** en el navegador: sumar "frágil", "este lado arriba" y "no mojar" como en el boceto F, cambiar uno de ícono y de color, redimensionar (no se deforma), "Ver cómo sale impreso" coincide, guardar/reabrir conserva; consola sin errores.

### Task 8: Cierre de la fase

- [ ] **Step 1:** suite completa (`--parallel 2`) + `flake8` + `check_frontend.mjs` → verde.
- [ ] **Step 2:** armar en el editor el bloque superior del diseño **E** (nombre en serif en dos renglones, títulos chicos en mayúsculas espaciadas y gris) y el saludo y la estampilla del **G** (manuscrita + ícono corazón); PDF y Labelary coinciden.
- [ ] **Step 3:** CLAUDE.md (Labels → System 1: tipografías, claves nuevas, ZPL nativo vs gráfico, íconos) y texto del mensaje de commit para el usuario.
