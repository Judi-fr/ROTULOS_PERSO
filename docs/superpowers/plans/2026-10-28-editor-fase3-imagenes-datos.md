# Editor de rótulos — Fase 3: imágenes propias y datos nuevos — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (elegido por el usuario) to implement this plan task-by-task, con superpowers:test-driven-development; el panel del editor con design-taste-frontend-v1. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** que el comerciante suba imágenes propias (varias por plantilla, además del logo) y las ponga en el rótulo con ajuste contener/cubrir, recorte circular y esquinas redondeadas; que el logo de la tienda también se pueda usar como una de esas imágenes; y que haya datos nuevos para insertar (nombre de pila, peso, bultos del pedido, transportista) — igual en lienzo, PDF y ZPL. Además, el lienzo puede mostrar los datos de ejemplo en lugar de los `{{marcadores}}`.

**Architecture:** las imágenes son un modelo nuevo `LabelImage` del comercio (archivo normalizado con Pillow, nombre aleatorio, público como los logos) con su API de subida y listado. En el diseño son una forma más de la fase 1 (`shapes[]` con `kind: "image"`), que referencia el id de la imagen o `"logo"` (el logo del remitente que ya recibe el render). Un módulo `label_images.py` concentra buscar el archivo y prepararlo (encuadre, recorte, máscara) en un `PIL.Image` RGBA que usan `shapes_pdf` (drawImage) y `shapes_zpl` (`^GF` con difuminado). Los datos nuevos se agregan a `build_label_context` y a los contextos de muestra; un endpoint devuelve el contexto de muestra para el lienzo.

**Tech Stack:** Django/DRF, Pillow, ReportLab, JS sin build.

**Spec:** `docs/superpowers/specs/2026-10-28-editor-rotulos-galeria-design.md` (punto 6 "Imágenes propias", punto 8 "Datos nuevos", decisión "las plantillas de la galería no traen imágenes de stock: va el logo de la tienda"). Fases 1 y 2 hechas (formas/color/capas; tipografía/íconos).

## Global Constraints

- **Regla del espejo** (CLAUDE.md): lienzo, PDF y ZPL iguales; el PDF tiene la última palabra.
- **Compatibilidad:** un diseño sin las claves nuevas imprime idéntico — `test_existing_standard_template_unchanged` (PDF y ZPL) sigue pasando sin tocarlo. Agregar claves a `SAMPLE_LABEL_CONTEXT` no cambia el PDF de la plantilla estándar (no las usa); verificarlo con ese mismo test.
- **Regla del rótulo:** nada de productos, precios, pago ni email/teléfono del comprador. Los datos nuevos son solo `nombre`, `peso`, `bultos_pedido`, `transportista`.
- **Datos del comercio:** las imágenes cuelgan de `teams.data_owner(request.user)` (el usuario de datos del comercio actual), nunca de `request.user`; el autor va en `created_by`.
- Forma imagen: `{id, kind: "image", image: <int id de LabelImage> | "logo", left, top, width, height, fit: "contain"|"cover" (default "contain"), circle: bool (default false), radius (mm, ya existe en rect; acá también), stroke/stroke_width/stroke_style (como rect, borde alrededor de la caja o del círculo), z, locked}`. Sin `text`, sin `fill`.
- Subida: archivo multipart o data URL, máximo 4 MB, PNG/JPEG/WebP/GIF (primer cuadro); se guarda normalizada (EXIF aplicado y quitado, lado mayor ≤ 2000 px, PNG si tiene transparencia, si no JPEG calidad 90) en `media/labels/images/<hex32>.<ext>`; máximo 300 imágenes activas por comercio. Borrar = `is_active=False` (las plantillas y versiones que la usan siguen imprimiéndola; el archivo nunca se borra).
- Una imagen que no existe (id desconocido) o un `"logo"` sin logo: no se dibuja nada y el render agrega el aviso `image_missing` a `warnings` (como `barcode_widened`); nunca 500.
- Térmica: la imagen se compone sobre blanco y se pasa a 1 bit con difuminado Floyd–Steinberg (como el logo de hoy) en `^GF` comprimido (`shapes_zpl.compress_rows`).
- Código en inglés, textos al usuario en español. **Sin commits** (los hace el usuario). Nunca tocar un test existente para que pase. Después de crear la migración, `python manage.py migrate`.
- Al cerrar cada tarea: `python manage.py test apps.labels` (y la app tocada) + `flake8`; si tocó frontend, `node frontend/tools/check_frontend.mjs`.

## Review Focus

1. **Imagen de otro comercio**: al guardar un diseño con `image: <id>` de una imagen que no es del comercio actual → 400 (no se puede "robar" una imagen por id). Una plantilla pública de otro comercio que se duplica sigue imprimiendo su imagen (el render no filtra por dueño). Tests en las tareas 1 y 2.
2. **Foto de celular rotada (EXIF) o enorme**: se guarda derecha y achicada; un PNG transparente conserva la transparencia; un archivo que no es imagen o de más de 4 MB → 400 claro en español. Tests en la tarea 1.
3. **Imagen borrada o inexistente**: el rótulo se imprime sin ella con aviso `image_missing`, sin 500, en PDF y ZPL. Tests en las tareas 2 y 3.
4. **`cover` con una imagen muy alargada y `circle` en una caja no cuadrada**: el recorte llena la caja sin deformar; el círculo es el mayor que entra, centrado (como los íconos). Tests en la tarea 2.
5. **`nombre` con nombres raros**: "  maría  josé  " → "María"; vacío → ""; "Dra. Ana Pérez" → "Dra." (primera palabra tal cual, sin adivinar títulos; documentado en CLAUDE.md). Test en la tarea 4.

---

### Task 1: Modelo `LabelImage` y API de imágenes

**Files:**
- Create: `backend/apps/labels/label_images.py` (normalización y búsqueda; la preparación para dibujar la agrega la tarea 2)
- Modify: `backend/apps/labels/models.py` (modelo), `backend/apps/labels/serializers.py` (`LabelImageSerializer`, reusa `ImageOrDataUrlField`/`_decode_data_url` con `max_bytes=4 MB`), `backend/apps/labels/views.py` (`LabelImageViewSet`), `backend/apps/labels/urls.py` (`router.register(r"images", ...)`), `Caddyfile` (agregar `/media/labels/images/*` a `@public_media`)
- Create: migración `backend/apps/labels/migrations/0013_labelimage.py` (makemigrations)
- Test: `backend/apps/labels/tests/test_label_images.py`

**Interfaces:**
- Produces: `LabelImage(owner FK user CASCADE, created_by FK user SET_NULL null, image ImageField upload_to="labels/images/", width_px, height_px, name CharField(120, blank), is_active bool default True, created_at)`; `label_images.MAX_UPLOAD_BYTES = 4 * 1024 * 1024`, `MAX_SIDE_PX = 2000`, `MAX_IMAGES_PER_OWNER = 300`; `label_images.normalize_upload(django_file) -> ContentFile` (nombre `<uuid hex>.png|.jpg`); `label_images.image_file(image_id) -> File | None` (por id, activa o no; `None` si no existe).
- API `/api/v1/labels/images/`: `GET` lista las activas del comercio (`id, url, name, width_px, height_px, created_at`, más nuevas primero, sin paginar), `POST` multipart `image` o JSON `{image: dataURL, name?}` → 201 con lo mismo, `DELETE <id>/` → `is_active=False`, 204. Permiso: `labels.template_create` para listar/subir y `labels.template_delete` para borrar (los mismos que plantillas, `HasRolePermission`). Auditar `template.update` no hace falta (no es plantilla); sin auditoría.

- [ ] **Step 1: tests que fallan** — `test_upload_multipart_and_data_url` (201, archivo bajo `labels/images/`, `owner` = usuario de datos del comercio — usar `team_helpers.new_business(person)`); `test_exif_rotation_applied_and_stripped` (JPEG 40×20 con Orientation=6 → guardado 20×40 y sin EXIF); `test_big_image_downscaled` (3000×1000 → 2000×667); `test_png_alpha_kept_as_png`; `test_rejects_non_image_and_too_big` (400, mensaje en español); `test_list_only_own_active` (otro comercio no ve; borrada no aparece); `test_delete_is_soft` (fila sigue con `is_active=False`, archivo sigue); `test_limit_per_business` (con `MAX_IMAGES_PER_OWNER` parcheado a 2, la tercera → 400); `test_permissions` (sin la access "plantillas" → 403); `test_caddyfile_serves_label_images_publicly` (como `test_private_files.py`: la ruta está en `@public_media`).
- [ ] **Step 2:** correr `python manage.py test apps.labels.tests.test_label_images` → FAIL (no existe).
- [ ] **Step 3:** implementar; `makemigrations labels` + `migrate`.
- [ ] **Step 4:** el módulo pasa; `python manage.py test apps.labels` y `flake8` verdes.

### Task 2: Forma imagen — validación y PDF

**Files:**
- Modify: `backend/apps/labels/design_shapes.py` (`SHAPE_KINDS` + `"image"`; claves `image`, `fit`, `circle`), `backend/apps/labels/serializers.py` (al validar un diseño en un serializer de plantilla/rótulo, los ids de `image` tienen que ser de `LabelImage` activas del comercio actual — chequeo en el serializer con `request`, no en `validate_design`, que es puro), `backend/apps/labels/label_images.py` (`prepare`), `backend/apps/labels/shapes_pdf.py` (`_draw_image`), `backend/apps/labels/label_rendering.py` (`draw_label_page` pasa `logo_file` y `warnings` a `draw_shapes`)
- Test: `backend/apps/labels/tests/test_image_shapes.py`

**Interfaces:**
- Consumes: `label_images.image_file` (tarea 1); `icons.icon_box` (fase 2) para el cuadrado del círculo.
- Produces: `label_images.prepare(source, box_w_px: int, box_h_px: int, fit: str, circle: bool, radius_px: float) -> PIL.Image.Image` (modo RGBA, exactamente `box_w_px × box_h_px`; `contain` = imagen escalada entera y centrada, transparente alrededor; `cover` = escalada hasta llenar y recortada al centro; `circle` = máscara del mayor círculo centrado; `radius_px` = esquinas redondeadas de la CAJA, con `contain` y con `cover`; se ignora con `circle`); `label_images.resolve_source(shape, logo_file) -> file-like | None` (`"logo"` → `logo_file`; int → `image_file`); `draw_shapes(pdf, shapes, ..., logo_file=None, warnings=None)` (firma de fase 1 + esos dos kwargs con default, así los llamadores viejos no cambian).
- PDF: `prepare` a 300 dpi del tamaño de la caja (tope 2400 px el lado), `pdf.drawImage(ImageReader(img), x, y, w, h, mask="auto")`; después el borde (`stroke`) con la forma de la caja (`roundRect`/`circle` del cuadrado de `icon_box` si `circle`).

- [ ] **Step 1: tests que fallan** — validación: acepta `{"kind":"image","image":5}` y `"logo"`, `fit` desconocido / `image` ausente / `text` presente → `ValidationError`; serializer de plantilla con id de imagen de OTRO comercio → 400 y con la propia → 201. `prepare`: imagen 400×100 roja en caja 100×100 `contain` → banda opaca centrada 100×25 y transparente arriba/abajo; `cover` → todo opaco; `circle` → esquinas transparentes y centro opaco. PDF (canvas grabador de `test_shapes_pdf.RecordingCanvas`, agregarle `drawImage`): dibuja una vez en la caja correcta; `image` inexistente → no dibuja y `warnings` contiene `image_missing`; `"logo"` sin `logo_file` → idem; un diseño sin formas imagen no consulta la base (assertNumQueries(0) sobre `render_label_pdf`).
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** verde + `apps.labels` + `flake8`.

### Task 3: Forma imagen — ZPL

**Files:**
- Modify: `backend/apps/labels/shapes_zpl.py` (`_image`), `backend/apps/labels/zpl.py` (`render_label_zpl` pasa `logo_file` y `warnings` a `shape_commands`)
- Test: `backend/apps/labels/tests/test_image_shapes_zpl.py`

**Interfaces:**
- Consumes: `label_images.prepare`, `resolve_source` (tarea 2); `compress_rows` (fase 1).
- Produces: `shape_commands(..., logo_file=None, warnings=None)`.
- Dibujo: `prepare` al tamaño de la caja en dots, componer sobre blanco, `convert("1")` (Floyd–Steinberg por defecto en Pillow), `^FO` + `^GFA` comprimido; el borde como las otras formas.

- [ ] **Step 1: tests que fallan** — imagen negra 50×50 `cover` → un `^GFA` cuyo tamaño en bytes = alto × ceil(ancho/8) de la caja en dots (8 dpmm y 12 dpmm); imagen inexistente → sin `^GF` y `image_missing`; `"logo"` con `logo_file` → `^GFA`; Labelary (`skipUnless ZPL_LABELARY_TESTS=1`) responde 200 con una foto `cover` + círculo.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** verde + `apps.labels` + `flake8`.

### Task 4: Datos nuevos y contexto de muestra

**Files:**
- Modify: `backend/apps/labels/label_rendering.py` (`build_label_context`, `build_preview_context`, `SAMPLE_LABEL_CONTEXT`, `build_shipment_context`), `backend/apps/labels/views.py` + `urls.py` (`GET /api/v1/labels/preview-context/`)
- Test: `backend/apps/labels/tests/test_new_markers.py`

**Interfaces:**
- Produces (claves del contexto, siempre strings): `nombre` = primera palabra de `destinatario` con la inicial en mayúscula y el resto como vino (`"  maría  josé "` → `"María"`, `""` → `""`); `peso` = `Order.total_weight_kg` como `"2,5 kg"` (coma decimal, sin ceros de más: `2.50` → `"2,5 kg"`, `3` → `"3 kg"`), `""` si no hay; `bultos_pedido` = `str(Order.package_count)` o `""` si es nulo/0; `transportista` = `Order.carrier` o `""`. En `build_shipment_context` (Labels API de Tiendanube) los mismos con lo que tenga el envío (`nombre` del destinatario; los otros `""` si no hay dato). Muestra (`build_preview_context`): `"nombre": "María"`, `"peso": "2,5 kg"`, `"bultos_pedido": "2"`, `"transportista": "Correo Argentino"`; `SAMPLE_LABEL_CONTEXT`: `"nombre": "María"`, `"peso": "1,2 kg"`, `"bultos_pedido": "1"`, `"transportista": "Andreani"`.
- `GET preview-context/` (`labels.render`, como `preview/`) → `{"context": build_preview_context(), "computed": {fecha, hora, fecha_hora, secuencia (muestra), bulto: "1", bultos: "2", bulto_de_bultos: "1 de 2"}}`.
- **No** cambia cuántos rótulos imprime un pedido: `{{bulto}}`/`{{bultos}}` siguen siendo la posición en el lote (Historia 29). `bultos_pedido` es solo el número cargado en el pedido.

- [ ] **Step 1: tests que fallan** — los casos de `nombre` y `peso` de arriba; pedido real con `package_count=3`, `carrier="OCA"` → `"3"`, `"OCA"`; pedido sin peso → `"peso": ""`; `preview-context/` 200 con las claves y 403 sin permiso; `test_existing_standard_template_unchanged` (PDF y ZPL) sigue verde.
- [ ] **Step 2:** FAIL. **Step 3:** implementar. **Step 4:** verde + `apps.labels` + `apps.integrations` (Labels API) + `flake8`.

### Task 5: Editor — imágenes

**Files:**
- Create: `frontend/assets/js/label_editor_images.js` (`window.LabelEditorImages`)
- Modify: `frontend/assets/js/label_editor_shapes.js` (`kind: "image"`: dibujo y panel), `frontend/editor_rotulos.html` (botón `+ Imagen` en "Formas y colores", carga del script)
- Test: navegador + `check_frontend.mjs`

**Interfaces:**
- Consumes: API de la tarea 1; logo actual del editor (el que ya se muestra para el campo `logo`) para `image: "logo"`.
- Produces: `LabelEditorImages.list() -> Promise<Array<{id,url,name,width_px,height_px}>>` (cacheada, se invalida al subir/borrar), `LabelEditorImages.upload(file) -> Promise<{id,url,...}>`, `LabelEditorImages.urlFor(image, logoUrl) -> string|null`, `LabelEditorImages.openPicker(onPick)` — un `<dialog>` "Imágenes del comercio": grilla de miniaturas, "Subir imagen" (input file, acepta arrastrar), "Usar el logo de la tienda", borrar (con `appConfirm`), mensajes con `showMessage`/texto inline; errores del servidor con `getErrorMessage`.
- Lienzo: la forma imagen es un `<div>` con un `<img>` `object-fit: contain|cover`, `border-radius` = radius en px o `50%` sobre un cuadrado centrado si `circle` (mismo cuadrado que `icon_box`), borde con el estilo de las otras formas; si la imagen no carga, caja punteada con el texto "Imagen no disponible".
- Panel de la forma imagen: "Cambiar imagen" (abre el picker), Ajuste (Contener/Cubrir), "Recorte circular", Esquinas (mm, deshabilitado con círculo), borde (los controles de trazo existentes); oculta relleno, texto y tipografía.

- [ ] **Step 1:** implementar (design-taste-frontend-v1 para el diálogo y el panel; consistente con el inspector).
- [ ] **Step 2:** en el navegador (cuenta de comerciante de prueba): subir una foto, ponerla de fondo en una franja con `cover` (como el diseño D), el logo de la tienda en círculo, una tercera imagen `contain` con esquinas; "Ver cómo sale impreso" coincide; guardar como plantilla y reabrir conserva todo; borrar una imagen de la biblioteca no la saca de la plantilla ya guardada; consola sin errores; `check_frontend.mjs` verde.

### Task 6: Editor — datos nuevos y "ver con datos de ejemplo"

**Files:**
- Modify: `frontend/assets/js/label_editor_markers.js` (catálogo: `nombre` "Nombre de pila", `peso` "Peso", `bultos_pedido` "Bultos del pedido", `transportista` "Transportista"), `frontend/editor_rotulos.html` (casilla "Ver con datos de ejemplo" en la barra del lienzo; con ella, los textos del lienzo — campos, textos fijos y texto de cajas — muestran el valor de muestra en vez de `{{...}}`; sin ella, como hoy)
- Test: navegador + `check_frontend.mjs`

**Interfaces:**
- Consumes: `GET /labels/preview-context/` (tarea 4), una vez por página.
- La sustitución es solo de presentación: `collectFields` y el guardado siguen leyendo el texto con marcadores (no guardar nunca el valor de muestra). Elegida por navegador (localStorage, envuelto en try/catch), apagada por defecto. Un marcador sin valor de muestra queda como está.

- [ ] **Step 1:** implementar. **Step 2:** en el navegador: "¡Hola, {{nombre}}!" muestra "¡Hola, María!" con la casilla y el marcador sin ella; un nombre largo en 2 renglones se ve cortado en el lienzo como en el PDF; editar el texto con la casilla puesta guarda el marcador, no "María" (reabrir y comprobar); consola sin errores; `check_frontend.mjs`.

### Task 7: Cierre de la fase

- [ ] **Step 1:** suite completa (`--parallel 2`) + `flake8` + `check_frontend.mjs` → verde.
- [ ] **Step 2:** armar en el editor, solo con la pantalla, la franja superior del diseño **D** (foto de fondo `cover` + logo circular) y el saludo del **G** con `{{nombre}}` ("¡Hola, María!"); PDF y Labelary coinciden con el lienzo; anotar lo que el editor no pueda hacer.
- [ ] **Step 3:** CLAUDE.md (Labels → System 1: imágenes del comercio, forma `image`, `"logo"`, `image_missing`, datos nuevos, `preview-context/`, Caddy) y mensaje de commit para el usuario.
