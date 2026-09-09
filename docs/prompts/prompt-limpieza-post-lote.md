# Prompt para Claude Code — Tres arreglos de deuda técnica

ROTULOS_PERSO (Buspack). Respetá `CLAUDE.md`. **No `git pull/push/commit`.**
Escribí el código directo: no hace falta auditar el proyecto.

Son tres cosas independientes que quedaron del trabajo de rótulos. Hacelas todas.

---

## 1. Sacar el rasterizador manual: el endpoint de códigos devuelve SVG

En `apps/labels/rendering.py` hay una función (`render_code_png`) que recorre a mano los `Rect` y
`String` de un `Drawing` de ReportLab y los pinta con Pillow. Se escribió porque ReportLab 5.x
mueve `renderPM` detrás de `rlPyCairo`, que arrastra `libcairo` como dependencia del sistema —
justo lo que se evitó al descartar WeasyPrint. La solución es correcta pero deja código propio
manteniendo algo que la librería ya sabe hacer, y lo usa **solo** la vista previa del editor.

Reemplazalo por **`reportlab.graphics.renderSVG`**, que es Python puro, viene con ReportLab y no
necesita nada del sistema:

- `GET /api/v1/labels/barcode/` pasa a devolver **SVG** (`Content-Type: image/svg+xml`) en vez de
  PNG. Mismos query params (`type`, `data`, medidas) y mismo permiso `labels.view`.
- Borrá `render_code_png` y todo lo que exista solo para sostenerlo (el recorrido manual de shapes
  y los imports de Pillow que queden sin uso en ese módulo). Pillow **sigue haciendo falta** en el
  proyecto para los `ImageField` de logo/miniatura: no lo saques de `requirements.txt`.
- El render a PDF **no cambia**: ahí los códigos se dibujan como vectores directo en el canvas
  (`Drawing.drawOn`) y nunca pasaron por el rasterizador.

En el editor (`frontend/pedidos/diseñorotulos.html`), el `<img>` que muestra la vista previa del
QR y del código de barras sigue funcionando igual: un blob SVG se muestra en un `<img>` sin
problema. Ajustá el `fetchCodePreview` solo en lo que haga falta por el cambio de tipo de
contenido.

---

## 2. Tests de permisos: sacar los números escritos a mano

En `apps/accounts/tests.py`, el catálogo de permisos está duplicado en dos sets literales
(`ALL_PERMISSIONS`, `ME_VIEW_PERMISSIONS`) **y además** en conteos numéricos sueltos: `25` → `29`,
`len(resp.data), 29`, y la cuenta `29 + 3 * 16`. Cada permiso nuevo obliga a tocar esos números a
mano; ya pasó tres veces en un día, y el día que alguien se olvide el test falla por una razón que
no tiene nada que ver con lo que rompió.

**Los dos sets literales se quedan** — son la guarda de verdad: dicen qué permisos se espera que
existan, y si los derivás de `permissions_map.PERMISSIONS` el test deja de comprobar nada (estaría
comparando el código consigo mismo).

Lo que hay que eliminar son los **números**: reemplazá cada literal por la expresión equivalente
sobre esos sets — `len(self.ALL_PERMISSIONS)`, `len(self.ME_VIEW_PERMISSIONS)`, y la cuenta de
asignaciones armada a partir de ellos en vez de `29 + 3 * 16`. Que no quede ningún conteo de
permisos escrito a mano en el archivo. Dejá un comentario corto explicando de dónde sale cada
cuenta.

No agregues tests nuevos ni toques la lógica de los existentes: esto es solo reemplazar constantes
por expresiones.

---

## 3. Ordenar los prompts sueltos

En la raíz del repo hay seis archivos `prompt-*.md` (`prompt-auditoria-soporte-dashboard.md`,
`prompt-reportes.md`, `prompt-crud-rotulos.md`, `prompt-pagina-rotulos.md`,
`prompt-fixes-infra.md`, `prompt-render-servidor.md`, `prompt-qr-codigos-barras.md`,
`prompt-lote-y-documentos.md` — los que estén).

Movelos a **`docs/prompts/`** conservando el nombre. Son documentación de cómo se pidió cada
módulo, así que se conservan; solo dejan de estar en la raíz. Si `.gitignore` los estuviera
excluyendo, dejalo como está — no cambies el tracking, solo la ubicación.
