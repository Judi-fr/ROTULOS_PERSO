# Foto → plantilla del editor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (decided with the user: plans run in this session, one final review). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que un rótulo leído desde una foto (`importar_rotulo.html`) se abra en el editor de rótulos como una plantilla nueva, lista para ajustar, guardar e imprimir pedidos.

**Architecture:** hoy la lectura de la foto (`apps/processing`) produce una propuesta en el formato de "diseños por elementos" (Sistema 2, milímetros), que ningún flujo de impresión usa. Se agrega un traductor puro propuesta → `design` de `LabelTemplate` (Sistema 1, porcentajes), expuesto en un GET por importación; el editor lo carga como punto de partida (`?nueva=plantilla&importacion=<id>`), igual que una plantilla de la galería. El guardado viejo como diseño por elementos se mantiene como botón secundario (decisión del usuario). El catálogo que lee Claude suma código de barras y número de seguimiento.

**Tech Stack:** Django REST, `apps.processing`, `apps.labels` (validación `serializers.validate_design`, render `label_rendering`), frontend sin build.

**Spec:** conversación del 2026-10-30 (decisiones abajo, en Global Constraints).

## Global Constraints

- El botón principal de la revisión es "Abrir en el editor de rótulos"; "Guardar como diseño por elementos" (el POST a `/labels/element-layouts/` de hoy) queda como secundario.
- Variables nuevas del catálogo: `codigo_barras` (tipo `codigo_barras`, "Código de barras") y `numero_seguimiento` (tipo `texto`, "Número de seguimiento"), `is_system=True`, con descripciones escritas para que Claude las lea (las lee `schema.build_system_prompt`).
- Nunca se guarda nada al traducir: la plantilla se crea recién cuando el comerciante toca "Guardar plantilla" en el editor (es del comercio, `data_owner`).
- El `design` resultante tiene que pasar `apps.labels.serializers.validate_design` y dibujarse con `render_label_pdf` sin errores.
- Textos al usuario en español; código en inglés; claves del `design` existentes sin renombrar.
- Una página por función: la traducción vive en el backend; `importar_rotulo.js` y el editor solo la piden y la muestran.

## Review Focus

1. Elementos que se salen del rótulo (x + ancho > 100 %): se acotan a 0–100, el diseño valida igual.
2. Dos elementos de la misma variable (dos "destinatario"): el primero va al campo, el otro a `omitted` con el motivo; nunca se pierde en silencio.
3. Una variable que el admin creó y el editor no conoce: no se inventa un marcador; va a `omitted`.
4. Medidas fuera de los límites de una plantilla (5–30 × 5–40 cm): se acotan a esos límites.
5. Importación de otro usuario o todavía sin propuesta: 404 / 400, no 500.

---

### Task 1: Catálogo — código de barras y número de seguimiento

**Files:**
- Create: `backend/apps/labels/migrations/0016_seed_barcode_tracking_variables.py`
- Test: `backend/apps/processing/tests/test_template_from_import.py`

**Interfaces:**
- Produces: `LayoutVariable` filas `codigo_barras` (data_type `codigo_barras`, order 90) y `numero_seguimiento` (data_type `texto`, order 100).

- [ ] **Step 1: test** `test_catalog_has_barcode_and_tracking`: ambas existen, activas, de sistema, con descripción no vacía; y `build_schema(LayoutVariable.objects.filter(is_active=True))` las incluye en el enum de variables.
- [ ] **Step 2: correr y ver que falla** — `python manage.py test apps.processing.tests.test_template_from_import`.
- [ ] **Step 3: migración** idempotente (`get_or_create` por `code`), reverse que borra solo esas dos si ningún `LayoutElement` las usa.
- [ ] **Step 4: correr, migrar la base local** (`python manage.py migrate labels`).

### Task 2: Traductor propuesta → design

**Files:**
- Create: `backend/apps/processing/to_template.py`
- Test: `backend/apps/processing/tests/test_template_from_import.py`

**Interfaces:**
- Produces: `proposal_to_template(proposal: dict) -> dict` con `{"name": str, "width_cm": float, "height_cm": float, "design": dict, "omitted": [{"label": str, "reason": str}]}`.

Reglas (porcentaje = `x_mm / width_mm * 100`, redondeado a 1 decimal, acotado 0–100):
- `remitente`, `destinatario`, `domicilio` → mismo campo; `codigo_postal` → `cp`; `localidad_provincia` → `localidad`; `numero_pedido` → `pedido`. Campo: `left`, `top`, `width` (solo si < 100), `font_size` (de `style.tamano_pt`), `bold` (de `negrita`), `align` (`izquierda/centro/derecha` → `left/center/right`, se omite `left`).
- `numero_seguimiento` → un texto fijo en `texts` con `text: "{{tracking}}"`.
- `texto_estatico` → `texts` con su `content` y la misma posición/estilo.
- `logo_empresa` → `design["logo"] = {left, top}`.
- `qr` / `qr_envio` → `design["qr"] = {left, top, size}` (lado en cm = el menor de ancho/alto del elemento).
- `codigo_barras` → `design["barcode"] = {left, top, width, height}` en cm.
- `linea` → forma `{"kind": "line", ...}` horizontal (`height: 0`) si es más ancha que alta, si no vertical (`width: 0`); `recuadro` → `{"kind": "rect", "fill": None, "stroke": "#000000"}`. Ids `s1`, `s2`…
- Duplicados y variables desconocidas → `omitted`.
- Medidas: cm = mm / 10, acotadas a 5–30 × 5–40; nombre = el de la propuesta.

- [ ] **Step 1: tests** (una propuesta armada a mano, en mm, como la que guarda `agent.build_proposal`):
  - `test_text_variables_map_to_fields` — `destinatario` con x 10 mm / y 30 mm en 100×150 → `design["destinatario"] == {"left": 10.0, "top": 20.0, ...}`; `codigo_postal` → `cp`.
  - `test_style_is_translated` — `tamano_pt 14, negrita, alineacion centro` → `font_size 14, bold True, align "center"`.
  - `test_static_text_and_tracking_go_to_texts` — `texto_estatico "CP:"` y `numero_seguimiento` → dos entradas en `texts`, la segunda con `"{{tracking}}"`.
  - `test_codes_and_logo` — `qr` 30×30 mm → `qr.size == 3.0`; `codigo_barras` 60×15 mm → `barcode.width == 6.0, height == 1.5`; `logo_empresa` → `logo` con left/top.
  - `test_lines_and_boxes_become_shapes` — línea ancha → `kind line, height 0`; línea alta → `width 0`; recuadro → `kind rect, stroke "#000000"`.
  - `test_duplicates_and_unknown_are_reported` — dos `destinatario` y una variable `cuit_cliente` → un solo campo y dos `omitted`.
  - `test_out_of_bounds_and_sizes_are_clamped` — x 95 mm, ancho 30 mm en 100 mm → left ≤ 100; rótulo 350×20 mm → 30 × 5 cm.
  - `test_result_validates_and_renders` — el `design` pasa `validate_design` y `render_label_pdf(..., context=SAMPLE_LABEL_CONTEXT)` devuelve bytes que empiezan con `%PDF`.
- [ ] **Step 2: ver que fallan.**
- [ ] **Step 3: implementar `proposal_to_template`.**
- [ ] **Step 4: ver que pasan; `flake8`.**

### Task 3: Endpoint

**Files:**
- Modify: `backend/apps/processing/views.py` (acción en `LabelImportViewSet`)
- Test: `backend/apps/processing/tests/test_template_from_import.py`

**Interfaces:**
- Consumes: `proposal_to_template` (Task 2).
- Produces: `GET /api/v1/processing/label-imports/<id>/template/` → la salida de `proposal_to_template` (200); sin propuesta → 400 `{"detail": "Esta importación todavía no tiene una lectura para abrir."}`; de otra persona → 404 (el queryset del viewset ya filtra). Permiso `processing.import`, igual que el resto del viewset.

- [ ] **Step 1: tests** `test_endpoint_returns_template`, `test_endpoint_404_for_someone_else`, `test_endpoint_400_without_proposal`.
- [ ] **Step 2–4:** fallan → implementar (`@action(detail=True, methods=["get"])`) → pasan.

### Task 4: Pantallas

**Files:**
- Modify: `frontend/importar_rotulo.html`, `frontend/assets/js/importar_rotulo.js` (botón principal nuevo, el de guardar pasa a secundario)
- Modify: `frontend/editor_rotulos.html` (carga `?importacion=<id>`)

**Interfaces:**
- Consumes: el GET de Task 3.

- [ ] **Step 1:** en la revisión, "Abrir en el editor de rótulos" (primario) lleva a `editor_rotulos.html?nueva=plantilla&importacion=<id>`; "Guardar como diseño por elementos" queda secundario con el mismo comportamiento de hoy.
- [ ] **Step 2:** el editor, con `importacion`, pide el GET, pone las medidas, `applyFields(design)`, nombra la plantilla con `name` y muestra en el estado qué quedó afuera (`omitted`) o "Revisá posiciones y tamaños antes de guardar". Los campos de texto que la foto no tenía quedan ocultos (como ya pasa con el logo); "Insertar dato" los vuelve a mostrar. Error del GET → "No se pudo abrir la lectura de la foto. Empezando en blanco."
- [ ] **Step 3:** `node frontend/tools/check_frontend.mjs` limpio; probar en el navegador con una importación creada en la base local (sin `ANTHROPIC_API_KEY`: `LabelImport` con una propuesta cargada a mano por shell), abrirla en el editor, guardar la plantilla y ver "Ver cómo sale impreso".

### Task 5: Cierre

- [ ] Suite completa (`python manage.py test`), `flake8`, `makemigrations --check`.
- [ ] CLAUDE.md: sección "Photo import → label layout" con el paso nuevo, el endpoint y las dos variables.
- [ ] Una revisión final del conjunto.
