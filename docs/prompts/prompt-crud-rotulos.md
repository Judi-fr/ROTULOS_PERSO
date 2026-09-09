# Prompt para Claude Code — CRUD de rótulos (apps.labels)

Trabajás sobre ROTULOS_PERSO. Respetá `CLAUDE.md`: inspeccionar antes de modificar, cambio mínimo,
validación/permisos/reglas de negocio en el **backend** (el frontend solo presenta), identidad
siempre desde `request.user` (JWT), URLs reales (`http://127.0.0.1:8000/api/v1/`), no borrar los
archivos de Google/OAuth, y **nada de `git pull/push/commit`**.

## Contexto de negocio (Buspack)

El sistema es para **Buspack**, una empresa de distribución de paquetería liviana a nivel nacional
(Argentina). Opera con empresas de micros de larga distancia como partners, y su fortaleza es la
red: más de **500 puntos de venta** y más de **600 destinos**.

Esto define qué es un "rótulo" acá: **no es una etiqueta de producto, es la etiqueta de encomienda
que se pega al paquete** que viaja en el micro. Por eso los campos del editor son remitente,
destinatario, domicilio, CP, localidad/provincia, N° de pedido y QR — es una guía de despacho.
El QR es lo que se escanea en el punto de venta y en la terminal.

Consecuencias concretas para esta tarea:

- Usá el vocabulario del negocio en nombres visibles, docstrings y textos de UI: **encomienda**,
  **envío**, **remitente**, **destinatario**, **destino**, **punto de venta**, **guía**. Evitá
  genéricos como "item", "producto" o "cliente" a secas donde corresponde "destinatario".
- El campo `client` de `Label` es el **destinatario** de la encomienda: nombralo y documentalo así.
- El vínculo opcional con `Order` es el caso central, no un extra: un rótulo normalmente pertenece
  a un envío concreto. Cuando `order` está seteado, ofrecé autocompletar destinatario, domicilio,
  CP y localidad desde la `Address` de ese pedido (el usuario después puede editarlos en el diseño).
- El rótulo se **imprime**: respetá las medidas en centímetros y no rompas el export a PNG/PDF que
  el editor ya tiene. Cualquier cambio de layout tiene que seguir siendo imprimible a escala real.

**Lo que NO hay que hacer en esta tarea** (queda para más adelante, no lo inventes ahora): todavía
no existe un catálogo de puntos de venta ni de destinos — `Address` guarda ciudad y provincia como
texto libre, y `Order.carrier` (la empresa de micros que hace el tramo) espera la API de tracking
del partner. No crees modelos `PickupPoint` ni `Destination` en este CRUD ni asumas que existen.

## Punto de partida (verificalo antes de escribir código)

- `backend/apps/labels/` está **vacía**: `models.py` sin modelos, `views.py` sin vistas,
  `urls.py` con `urlpatterns = []`. Está montada en `config/urls.py` bajo `/api/v1/labels/`.
- El frontend ya tiene el editor hecho pero **huérfano**:
  - `frontend/pedidos/diseñorotulos.html` — editor completo (arrastre de campos, logo, QR, tamaño
    en cm, export PNG/PDF). Importa `./api.js`, **que no existe**.
  - `frontend/plantillas_rotulos.html` — grilla/listado. Referencia
    `assets/js/dashboard_rotulos.js` y `assets/css/gestionrotulos.css`, **que tampoco existen**.
  - Ambas usan `<app-sidebar>` de `assets/html/aside.js`.
- El editor ya define el formato del diseño, y **hay que respetarlo** en vez de inventar otro:
  `{nombre, cliente, thumbnail, size:{widthCm,heightCm}, logo, fields:{<campo>:{left,top,text?}}}`,
  con `left`/`top` en **porcentaje** del rótulo (así el diseño sobrevive a un cambio de tamaño).
  Campos actuales: `logo`, `qr`, `remitente`, `destinatario`, `domicilio`, `cp`, `localidad`, `pedido`.

## Backend

### Dependencia nueva

Agregá **`Pillow`** a `backend/requirements.txt` (hoy no está) — hace falta para `ImageField`.
`MEDIA_URL`/`MEDIA_ROOT` ya están configurados en `settings/base.py` y servidos en DEBUG.

### Modelos (`apps/labels/models.py`)

**`LabelTemplate`** — diseño base reutilizable:

- `name` (`CharField`), `description` (blank), `owner` (FK a User, `null=True`, `SET_NULL`:
  las plantillas del sistema no tienen dueño), `is_public` (`BooleanField`, default `False`:
  visible para todos los usuarios).
- `width_cm` / `height_cm` (`DecimalField(max_digits=5, decimal_places=2)`), con los mismos
  rangos que valida el editor: ancho 5–30, alto 5–40.
- `design` (`JSONField`): el bloque `fields` con las posiciones por defecto.
- `preview` (`ImageField(upload_to="labels/templates/", null=True, blank=True)`).
- `created_at` / `updated_at`, `ordering = ["name"]`.

**`Label`** — el rótulo concreto del usuario:

- `user` (FK a User, `CASCADE`, `related_name="labels"`), `template` (FK a `LabelTemplate`,
  `null=True`, `SET_NULL`: si se borra la plantilla, el rótulo conserva su diseño propio).
- `name` (`CharField`), `client` (`CharField`, blank — el "cliente/destinatario" del editor).
- `order` (FK a `apps.orders.Order`, `null=True`, `blank=True`, `SET_NULL`,
  `related_name="labels"`): vínculo opcional con un pedido.
- `width_cm` / `height_cm`, `design` (`JSONField`: el `fields` completo con posiciones y textos).
- `logo` (`ImageField(upload_to="labels/logos/", null=True, blank=True)`),
  `thumbnail` (`ImageField(upload_to="labels/thumbs/", null=True, blank=True)`).
- `is_active` (`BooleanField`, default `True`) — **el borrado es soft-delete**, igual que usuarios:
  nunca se destruye un rótulo de forma permanente.
- `created_at` / `updated_at`, `ordering = ["-updated_at"]`.

### Serializers

- Validá `design` de verdad, no lo aceptes crudo: debe ser un dict cuyas claves estén dentro del
  set de campos conocidos, y cada valor un dict con `left`/`top` numéricos entre 0 y 100
  (`text` opcional, string). Un `design` inválido devuelve 400 con mensaje claro.
- Validá los rangos de `width_cm`/`height_cm` en el serializer (no confíes en el `min`/`max` del HTML).
- **Logo y miniatura**: el editor los produce hoy como data URL base64. Aceptá ambas formas —
  `multipart/form-data` con el archivo, o un string data URL que el serializer decodifica a
  `ContentFile` — pero guardá siempre en `ImageField`. Rechazá lo que no sea imagen y poné un tope
  de tamaño razonable (por ejemplo 2 MB el logo, 300 KB la miniatura).
- Si `order` viene seteado, validá que **ese pedido sea del propio usuario** (un usuario no puede
  colgar un rótulo de un pedido ajeno). El admin puede saltarse esa restricción.
- Devolvé las URLs de `logo`/`thumbnail` absolutas (usando `request` en el contexto), no la ruta cruda.

### Permisos

Permisos atómicos nuevos en `permissions_map.PERMISSIONS`, sembrados por migración idempotente
(mismo patrón que `0012_seed_support_permission.py`):

- `labels.view`, `labels.create`, `labels.edit`, `labels.delete` → los **cuatro roles** canónicos
  (self-service, igual que `orders.*`).
- `labels.view_all`, `labels.manage_templates` → **solo admin**.

Ojo con esto: los roles personalizados que ya existen en la base (`productor`, `creador`) **no**
reciben permisos por migración porque se crearon desde la UI. No los toques desde la migración; el
admin se los asigna desde la pantalla de Roles si corresponde.

Usá `HasRolePermission` en `get_permissions()`, nunca `IsAdminUser` suelto.

### Endpoints (`apps/labels/urls.py`, montado en `/api/v1/labels/`)

Con `SimpleRouter`, siguiendo el patrón de `apps.orders.views`:

- `LabelViewSet` — CRUD de los rótulos **propios**: el `get_queryset` se recorta siempre a
  `request.user` y a `is_active=True`. `destroy` hace soft-delete (`is_active=False`).
  Acción extra `POST /api/v1/labels/labels/<id>/duplicate/` que clona el rótulo con
  `name + " (copia)"` — es la forma de "partir de uno anterior".
- `LabelTemplateViewSet` — lectura para todos (plantillas públicas + las propias);
  crear/editar/borrar exige `labels.manage_templates`.
- Listado admin de todos los rótulos, permiso `labels.view_all`, solo lectura, paginado con la
  `pagination.py` existente y con filtros `search` (nombre/cliente/email del dueño), `user`,
  `template`, `is_active`, `date_from`/`date_to`. El queryset de `LabelViewSet` **no cambia**:
  esto es un endpoint aparte, igual que se hizo con los pedidos.

Los listados usan la paginación existente (page_size 20, shape `count/from/to/total_pages/...`).

### Auditoría

Si `apps.audit` ya existe en el repo, registrá `label.create`, `label.update`, `label.delete` y
`template.*` con su servicio `record(...)`. Si todavía no está, salteá esto sin crear nada.

## Frontend

Reutilizá las páginas que ya existen — **no las reescribas desde cero** — y creá los tres archivos
faltantes que hoy están rotos:

1. **`frontend/pedidos/api.js`** — el módulo que `diseñorotulos.html` ya importa, exportando
   `getRotulo`, `createRotulo`, `updateRotulo` (y `listRotulos`, `deleteRotulo`, `duplicateRotulo`).
   Debe usar el **mismo wrapper `apiFetch`** que el resto del frontend: header `Authorization`
   con el token de `localStorage`, manejo de 401 (limpiar sesión y volver a `index.html`) y de 403
   con `must_change_password` (redirigir a `cambiar-password.html`). Copiá el patrón de `ayuda.js`.
2. **`frontend/assets/js/dashboard_rotulos.js`** — listado de `plantillas_rotulos.html`: grilla de
   tarjetas con miniatura, nombre, cliente y fecha; buscador (el `#search` ya está en el HTML);
   modal de vista previa (los ids `modalOverlay`, `modalImg`, `modalNombre`, `modalCliente`,
   `modalFecha`, `modalCloseBtn` ya existen); y acciones Editar / Duplicar / Eliminar.
3. **`frontend/assets/css/gestionrotulos.css`** — estilos de esa grilla, consistentes con
   `gestionuser.css` / `pedidos.css`. Sin frameworks ni build step nuevos.

Además:

- En `diseñorotulos.html`, el `saveBtn` ya arma el payload correcto: adaptá `api.js` a **ese**
  formato en vez de cambiar el editor. Corregí el enlace `/frontend/pedidos/diseñorotulos.html`
  de `plantillas_rotulos.html` a una ruta relativa que funcione.
- Ambas páginas deben redirigir a `index.html` si no hay token, igual que el resto.
- En `DashboardView` (`apps/accounts/dashboard_views.py`), los ítems `labels` ("Mis rótulos") y
  `processing` están hoy con `enabled: False` y sin URL: pasá **`labels`** a
  `{"url": "plantillas_rotulos.html", "enabled": True}`. `processing` queda como está.

## Tests

Agregá tests nuevos en `apps/labels/tests.py` (sin tocar los existentes de otras apps) que cubran
al menos: un usuario ve solo sus rótulos y recibe 404 al pedir uno ajeno; el CRUD completo con un
`design` válido; un `design` inválido devuelve 400; el borrado es soft-delete y el rótulo
desaparece del listado pero sigue en la base; no se puede asociar un rótulo a un pedido ajeno;
un no-admin recibe 403 en el listado admin y en la creación de plantillas; y `duplicate` crea una
copia independiente del original.

Al terminar: `python manage.py test` en verde (no debe romperse ningún test existente) y actualizá
`CLAUDE.md` con los modelos, permisos y rutas nuevas de `apps.labels`.
