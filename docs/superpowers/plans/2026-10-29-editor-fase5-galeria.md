# Editor de rótulos — Fase 5: las ocho plantillas de la galería — Implementation Plan

> **For agentic workers:** superpowers:executing-plans (en la sesión, una revisión final). Galería con design-taste-frontend-v1.

**Goal:** que "+ Nueva plantilla" abra una galería con los ocho diseños (H, D, C, E, F, G, B, A) + "En blanco", y que elegir uno abra el editor con una copia editable.

**Architecture:** los siete diseños nuevos son datos en `backend/apps/labels/gallery.py` (`GALLERY`: clave, nombre, descripción, orden, diseño), armados SOLO con las claves que el editor ya edita (fases 1–4) — así se cumple "el editor tiene que poder construir cada diseño". Una migración los siembra como plantillas del sistema (`owner=None`, `is_public=True`) con un campo nuevo `LabelTemplate.gallery_order` (la estándar A queda última). La API de plantillas acepta `?gallery=1`. Página nueva `galeria_plantillas.html` + `assets/js/galeria_plantillas.js` (una página por función); "+ Nueva plantilla" de Mis plantillas apunta ahí; cada tarjeta abre `editor_rotulos.html?nueva=plantilla&template=<id>` (el editor ya carga una plantilla como punto de partida y guarda una copia nueva).

**Spec:** `docs/superpowers/specs/2026-10-28-editor-rotulos-galeria-design.md` ("La galería", punto 11). Bocetos: `.superpowers/brainstorm/40133-1793201660/content/disenios-*.html`.

## Global Constraints

- Orden: H Moderno, D Con la marca, C Destacado, E Editorial, F Banda lateral, G Postal, B Celdas, A Estándar. Todas 10×15 cm.
- Sin imágenes de stock: donde el boceto tiene una imagen de marca va la forma imagen `"logo"` (el logo de la tienda/comercio).
- La plantilla estándar (A) no cambia su diseño (los tests de hash siguen iguales); solo recibe `gallery_order`.
- Cada diseño: valida con `validate_design`; PDF y ZPL (8 y 12 dpmm) se dibujan; con el tracking de muestra de 19 dígitos ningún código queda `barcode_widened`/`barcode_unreadable`; QR y código de barras se leen en el raster de 203 dpi (`test_scanning.decode`); ningún dato del comprador fuera de la regla del rótulo.
- La migración es idempotente por nombre (no duplica si ya existe) y su reverse borra solo las que creó sin cambios.
- Miniaturas: `generate_template_thumbnails` las completa (el deploy ya lo corre).

## Review Focus

1. Un comercio sin logo: los diseños con `"logo"` se imprimen sin hueco feo ni error (solo el aviso en la vista previa).
2. Nombre y dirección largos (muestra de `build_preview_context`): nada se sale de su caja ni pisa otra cosa.
3. Elegir un diseño y guardarlo crea una plantilla propia; la del sistema no cambia.
4. Térmica: los colores de cada diseño quedan legibles (vista térmica / Labelary).

---

### Task 1: Diseños y migración — `gallery.py`, `gallery_order`, `0014`, `?gallery=1`, tests (`tests/test_gallery.py`).
### Task 2: Página de galería — HTML/JS/CSS, enlace desde Mis plantillas.
### Task 3: Verificación — navegador (abrir cada diseño, guardar copia), Labelary, revisión final, CLAUDE.md, mensaje de commit.
