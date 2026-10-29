# Editor de rótulos — Fase 4: deshacer, duplicar, alinear, zoom y vista térmica — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (elegido por el usuario: en la sesión, sin agentes por tarea, una revisión final). Panel con design-taste-frontend-v1. Steps use checkbox (`- [ ]`) syntax.

**Goal:** que el editor tenga deshacer/rehacer, duplicar, selección múltiple con alinear/distribuir, zoom y una vista "así sale en térmica", todo con atajos de teclado (pedido del usuario: "que funcionen con comandos como Ctrl+Z").

**Architecture:** el historial guarda instantáneas del diseño completo (`collectFields()` + medida) — una sola fuente de verdad, sin un "comando" por operación — y las graba solo cuando el diseño cambió (comparando el JSON) al terminar cada gesto (`pointerup`, `change`, `input` con demora, flechas). Restaurar = `applyFields` + medida. Alinear trabaja sobre las cajas del DOM relativas al rótulo y escribe en cada tipo de elemento con un adaptador. El zoom escala el tamaño CSS del rótulo (todo lo demás ya mide con `getBoundingClientRect`). La vista térmica la dibuja el servidor: el mismo PDF de "Ver cómo sale impreso", rasterizado a 203 dpi con la regla térmica de la fase 1.

**Tech Stack:** JS sin build; Django + pypdfium2 + Pillow para la vista térmica.

**Spec:** `docs/superpowers/specs/2026-10-28-editor-rotulos-galeria-design.md`, puntos 9 y 10.

## Global Constraints

- Espejo: nada de esto cambia lo que se imprime; el diseño guardado es el mismo con o sin estas funciones. Un diseño viejo abre igual.
- Atajos: Ctrl/⌘+Z deshacer; Ctrl/⌘+Shift+Z y Ctrl/⌘+Y rehacer; Ctrl/⌘+D duplicar; Supr/Retroceso borra la forma, línea o texto fijo elegido; Shift+clic suma/saca de la selección; Esc limpia la selección; zoom con Ctrl/⌘ + rueda sobre el lienzo y los botones. **Mientras se escribe** (input, textarea, select, texto editable) los atajos de deshacer/rehacer/duplicar/borrar NO se interceptan: el navegador hace su propio deshacer de texto.
- Cada botón muestra su atajo en el `title` ("Deshacer (Ctrl+Z)").
- Historial: máximo 100 pasos; abrir otro rótulo/plantilla lo reinicia; no se graba mientras se restaura; "Ver con datos de ejemplo" no genera pasos (el historial usa los marcadores).
- Zoom 50 %–300 %, pasos de 25 %; "Ajustar" vuelve a 100 %; recordado por navegador (localStorage en try/catch). No cambia el tamaño del rótulo guardado.
- Vista térmica: `POST /api/v1/labels/preview/` con `"thermal": true` devuelve PNG 1 bit a 203 dpi (8 dpmm): luminancia < 96 negro, > 245 blanco, en medio trama Bayer 4×4 (`design_shapes.THERMAL_*`, `shapes_zpl.BAYER_4X4`). Es una aproximación del ZPL (la Zebra dibuja el texto con su fuente); se aclara en el diálogo.
- Código en inglés, textos al usuario en español. Sin commits. No tocar tests existentes. `node frontend/tools/check_frontend.mjs` y `python manage.py test apps.labels` al final.

## Review Focus

1. Deshacer después de arrastrar, cambiar un estilo en el panel, agregar/borrar una forma, cambiar la medida: vuelve exactamente al estado anterior (comparar `collectFields()`).
2. Ctrl+Z mientras se escribe en un texto del rótulo deshace el texto (navegador), no el diseño.
3. Alinear con un elemento bloqueado en la selección: el bloqueado no se mueve y sirve de referencia.
4. Duplicar una caja con texto o una imagen: copia todo con id nuevo; duplicar no pisa el original.
5. Zoom al 200 %: arrastrar, el imán y el redimensionado siguen dejando las mismas posiciones en % que al 100 %.

---

### Task 1: Historial (deshacer/rehacer)

**Files:** Create `frontend/assets/js/label_editor_history.js` (`window.LabelEditorHistory`); Modify `frontend/editor_rotulos.html` (barra del lienzo con Deshacer/Rehacer, init, reset al cargar).

**Interfaces:** `LabelEditorHistory.init({capture: () => string, restore: (snapshot: string) => void, onChange: (state) => void})`, `.reset()` (la instantánea actual pasa a ser la base), `.record()` (graba si cambió), `.scheduleRecord(ms = 500)`, `.undo()`, `.redo()`, `.canUndo()`, `.canRedo()`. `capture` = `JSON.stringify({design: collectFields(), width, height})`; `restore` aplica medida + `applyFields(design)` + `codeConfig`, deselecciona todo.

- [ ] Implementar y conectar los disparadores: `pointerup` en el documento, `change` e `input` (demora 500 ms), `keyup` de flechas (demora), y `record()` explícito después de altas/bajas de formas, textos y líneas.
- [ ] Atajos y botones (deshabilitados sin pasos). Navegador: los 5 casos de Review Focus 1–2.

### Task 2: Duplicar y borrar

**Files:** Modify `label_editor_shapes.js` (`duplicateSelected()`: copia profunda, id nuevo con `newId`, +2 % en left/top recortado al rótulo, `z` = máx + 1), `editor_rotulos.html` (duplicar línea/texto fijo elegido; Supr borra la decoración elegida; botón "Duplicar" en los paneles de forma y de decoración).

- [ ] Implementar. Los campos de datos (destinatario, cp…) no se duplican (son únicos): Ctrl+D no hace nada con ellos.
- [ ] Navegador: duplicar caja con texto, imagen, ícono, línea y texto fijo; deshacer el duplicado.

### Task 3: Selección múltiple, alinear y distribuir

**Files:** Create `frontend/assets/js/label_editor_align.js` (`window.LabelEditorAlign`); Modify `editor_rotulos.html` y `label_editor_shapes.js` (avisar Shift+clic en vez de seleccionar uno solo).

**Interfaces:** `LabelEditorAlign.init({label, adapters})` con un adaptador por tipo `{box(item) -> {left, top, width, height} en %, move(item, dxPct, dyPct), locked(item)}` para `field`, `text`, `line`, `shape`; ítem = `{type, key}`. `.toggle(item)`, `.clear()`, `.items()`. Barra flotante sobre el lienzo con 2+ elegidos: alinear izquierda/centro/derecha, arriba/medio/abajo (referencia = la caja que encierra la selección; si hay bloqueados, la del primero bloqueado), distribuir horizontal/vertical (3+, espacios iguales entre cajas). Un elemento bloqueado no se mueve. Esc limpia. Al terminar, `rerender` + `LabelEditorHistory.record()`.

- [ ] Implementar (design-taste-frontend-v1 para la barra: íconos SVG en línea, compacta).
- [ ] Navegador: alinear a la izquierda un campo, una caja y un texto fijo; distribuir 3 cajas; con uno bloqueado; deshacer.

### Task 4: Zoom

**Files:** Modify `editor_rotulos.html` (botones −/+/porcentaje/"Ajustar" en la barra del lienzo; el ancho/alto CSS del rótulo = medida × zoom; Ctrl+rueda sobre el lienzo).

- [ ] Implementar; `applyRenderMetrics` ya mide desde el DOM. Navegador: Review Focus 5 (posiciones guardadas iguales al 100 % y al 200 %).

### Task 5: Vista térmica

**Files:** Modify `backend/apps/labels/views.py` (`PreviewLabelView`: `thermal` → PNG), Create `backend/apps/labels/thermal_preview.py` (`thermal_png(pdf_bytes, dpmm=8) -> bytes`), Test `backend/apps/labels/tests/test_thermal_preview.py`; Modify `editor_rotulos.html` (botón "Vista térmica" que abre el mismo diálogo de vista previa con la imagen y la aclaración).

- [ ] Tests primero: un rect negro → píxeles negros; gris #9ca3af → mezcla de negros y blancos (trama); #f3f4f6 → blanco; tamaño = medida × 8 dpmm; `Content-Type: image/png`; sin `thermal` sigue devolviendo PDF.
- [ ] Implementar; navegador.

### Task 6: Cierre

- [ ] `apps.labels` + `flake8` + `check_frontend.mjs`; una revisión final (modelo más capaz); CLAUDE.md; mensaje de commit.
