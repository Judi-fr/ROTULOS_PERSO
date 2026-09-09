# Prompt para Claude Code — Generación por lote + sección "Mis documentos"

ROTULOS_PERSO (Buspack — paquetería liviana). Respetá `CLAUDE.md`. **No `git pull/push/commit`.**
Escribí el código directo: no hace falta auditar el proyecto.

Continúa los dos puntos anteriores: ya existe el render server-side en `apps/labels/rendering.py`
(`render_label_pdf`, `build_label_context`, marcadores `{{...}}`) y los códigos QR / de barras.
Ahora se generan muchos rótulos de una y el resultado queda guardado.

La app **`apps.documents` está vacía** (models, views y urls sin nada, ya montada en
`/api/v1/documents/`) y el ítem "Mis documentos" del menú del dashboard está en `enabled: False`.
Esta tarea las llena: un lote produce un documento, y los documentos viven ahí.

---

## Parte 1 — `apps.documents`: el archivo generado

### Modelo `Document`

- `user` (FK a User, `CASCADE`, `related_name="documents"`).
- `name` (`CharField`) — legible, ej. `"Rótulos 12/09 (35 envíos)"`.
- `kind` (`TextChoices`): `labels_batch` por ahora; el modelo tiene que servir para otros tipos
  de documento más adelante.
- `file` (`FileField(upload_to="documents/%Y/%m/", null=True, blank=True)`).
- `status` (`TextChoices`): `processing`, `ready`, `failed`. Arranca en `processing` y termina
  en uno de los otros dos.
- `item_count` (`PositiveIntegerField`, default 0) — cuántos rótulos entraron.
- `size_bytes` (`PositiveIntegerField`, default 0).
- `error_message` (`TextField`, blank) — qué falló, para no dejar al usuario con un
  "falló" sin explicación.
- `is_active` (default `True`) — el borrado es **soft-delete**, como en el resto del proyecto.
- `created_at` / `updated_at`, `ordering = ["-created_at"]`.

### Endpoints (`/api/v1/documents/`)

- `GET /` — lista paginada de los documentos **propios** (queryset recortado a `request.user`,
  `is_active=True`), con filtros `search` (nombre), `kind`, `status`, `date_from`/`date_to`.
  Permiso `documents.view`.
- `GET /<id>/` — detalle.
- `GET /<id>/download/` — devuelve el archivo con `Content-Disposition: attachment`.
  Un documento en `processing` o `failed` responde 404, no un archivo vacío.
- `DELETE /<id>/` — soft-delete. Permiso `documents.delete`.
- Listado admin de todos los documentos con permiso `documents.view_all`, endpoint aparte y solo
  lectura, igual que se hizo con pedidos y rótulos.

Permisos nuevos sembrados por migración idempotente: `documents.view`, `documents.delete` para los
cuatro roles canónicos; `documents.view_all` solo admin.

---

## Parte 2 — Generación por lote

### Endpoint

`POST /api/v1/labels/batch/`, permiso nuevo `labels.batch` (los cuatro roles).

```json
{
  "template_id": 3,
  "order_ids": [12, 13, 14],
  "output": "pdf"
}
```

- **Selección de qué imprimir**: `order_ids` (lista explícita) **o** `label_ids` (rótulos ya
  guardados) **o** un bloque `filters` con `status` y `date_from`/`date_to` para tomar todos los
  pedidos que cumplan. Exactamente una de las tres; si vienen dos o ninguna, 400.
- Los pedidos y rótulos tienen que ser **del usuario**. Un admin con `labels.view_all` puede
  generar sobre cualquiera. Si algún id no es suyo, 400 diciendo cuál — nunca lo ignores en
  silencio.
- `template_id` es obligatorio con `order_ids`/`filters` (define el diseño); con `label_ids` cada
  rótulo ya trae el suyo.
- `output`: `"pdf"` (default) → **un solo PDF con una página por rótulo**; `"zip"` → un ZIP con un
  PDF por rótulo, nombrados por el código de seguimiento (`BP-000123.pdf`).

### Cómo se ejecuta

Hacelo **síncrono**, sin cola de tareas: el proyecto no tiene Celery ni Redis y no es momento de
sumarlos. Para que eso sea sostenible:

- Tope de `LABELS_BATCH_MAX_ITEMS` (setting nuevo, default **200**) por lote. Pasarse devuelve 400
  con el número máximo, no un timeout.
- El `Document` se crea **antes** de empezar a renderizar, en estado `processing`, y la respuesta
  es un **202** con su id: así el frontend ya tiene algo que mostrar aunque el request tarde.
- Al terminar, el documento pasa a `ready` con el archivo, el `item_count` y el `size_bytes`.
  Si algo explota, queda en `failed` con el `error_message`, nunca a medio camino.
- Un rótulo que falle individualmente **no tumba el lote**: se saltea, se cuenta, y el documento
  queda `ready` con el detalle de cuántos se omitieron y por qué.
- Generá página por página sobre un mismo canvas de ReportLab en vez de armar N PDFs y
  concatenarlos: con 200 rótulos la diferencia de memoria es real.

Dejá el render de cada rótulo pasando por las mismas funciones de `rendering.py` que usa el
endpoint individual — un solo camino de generación, sin una segunda implementación para el lote.

Registrá el lote en auditoría (`document.create` o `label.batch`) si `apps.audit` existe.

---

## Parte 3 — Frontend

### `frontend/documentos.html` + `assets/css/documentos.css` + `assets/js/documentos.js`

Misma estructura visual que `pedidos.html` / `rotulos.html`: `div.layout` → `header.topbar`
(avatar, nombre, email, "Volver al panel", "Cerrar sesión") → `main.main` con `.main-heading`
("Mis documentos") y secciones `profile-card`. Fuente Karla por CDN, nada de Bootstrap ni MDI,
script clásico al final, `apiFetch` con el mismo manejo de 401 y 403 que el resto.

Contenido: tabla o lista de documentos con nombre, tipo, cantidad de rótulos, tamaño, fecha y
estado. Botón de descarga en los `ready`; en los `processing`, un indicador y refresco automático
cada pocos segundos hasta que cambien; en los `failed`, el mensaje de error visible. Buscador y
filtro por estado contra el backend. Eliminar con confirmación. Estados de carga y vacío
explícitos.

### Cableado

- En `dashboard_views.py`, el ítem `documents` pasa a
  `{"label": "Mis documentos", "url": "documentos.html", "enabled": True}`.
- En `rotulos.html`, agregá un botón **"Generar por lote"** que abra un panel donde se elige la
  plantilla y los pedidos (o el rango de fechas), dispare el `POST` y, con el 202 en mano,
  redirija a `documentos.html` para seguir el progreso ahí.

## Test

Uno solo: que un lote con un `order_id` que no es del usuario devuelva 400 y **no** cree ningún
`Document`. Es la puerta por la que se filtrarían datos de otro cliente, y el caso que conviene
tener clavado antes de que el lote crezca.
