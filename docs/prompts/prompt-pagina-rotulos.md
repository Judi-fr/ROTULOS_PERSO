# Prompt para Claude Code — Página nueva "Mis rótulos" (reemplaza plantillas_rotulos.html)

Trabajás sobre ROTULOS_PERSO (Buspack — distribución de paquetería liviana; un rótulo es la
etiqueta de encomienda que se pega al paquete). Respetá `CLAUDE.md`. **No `git pull/push/commit`.**
Andá directo a escribir el código: no hace falta auditar el proyecto ni escribir tests para esto.

## Problema

`plantillas_rotulos.html` es una página vieja, de otra etapa del proyecto: usa el web component
`<app-sidebar>` de `assets/html/aside.js` más Bootstrap 4 y Material Design Icons por CDN, con
`assets/css/gestionrotulos.css`. No se parece en nada al resto del frontend actual y está rota.
El ítem "Mis rótulos" del dashboard apunta ahí.

## Qué hacer

Creá una página nueva, `frontend/rotulos.html`, con su `assets/css/rotulos.css` y
`assets/js/rotulos.js`, que **replique el patrón visual y estructural de `frontend/pedidos.html`**
(esa es la referencia de diseño; copiá su esqueleto y adaptá el contenido):

- `<div class="layout">` → `<header class="topbar">` con avatar (`#userAvatar`), nombre
  (`#userName`) y email (`#userEmail`) leídos de `localStorage.user`, más `.topbar-actions` con un
  `<a class="btn btn-outline" href="dashboard.html">Volver al panel</a>` y el botón
  `#logoutBtn`.
- `<main class="main">` con `.main-heading` (`<h1>Mis rótulos</h1>` y un subtítulo del estilo
  "Creá y gestioná los rótulos de tus encomiendas.").
- `<p id="pageMessage" class="page-message" role="status" aria-live="polite">` para mensajes.
- Secciones con `class="profile-card"` + `.profile-card-title` + `.profile-card-body`, igual que
  pedidos.html.
- Fuente Karla por CDN (mismo `<link>` que pedidos.html) y **nada de Bootstrap, MDI ni
  `<app-sidebar>`**. `<script src="assets/js/rotulos.js"></script>` al final del body, script
  clásico (no `type="module"`).

### Contenido de la página

1. **Barra superior de la sección**: buscador (`#searchInput`, filtra por nombre y destinatario) y
   botón primario "+ Nuevo rótulo" que lleva al editor (`pedidos/diseñorotulos.html`).
2. **Grilla de rótulos**: tarjetas con la miniatura, el nombre, el destinatario, el tamaño en cm,
   el N° de pedido asociado si lo tiene, y la fecha de última modificación. Cada tarjeta con
   acciones **Editar** (abre el editor con el id en la query string), **Duplicar** (llama a
   `duplicate/`) y **Eliminar** (soft-delete, con confirmación).
3. **Vista previa**: al hacer clic en la miniatura, un modal simple que muestra la imagen grande
   con nombre, destinatario y fecha. Un solo modal reutilizado, cerrable con el botón, con clic en
   el fondo y con `Escape`.
4. **Estados vacíos y de carga** explícitos: "Cargando rótulos...", y si no hay ninguno, un
   mensaje con el botón para crear el primero. Si la búsqueda no encuentra nada, decilo — nunca
   dejes la grilla en blanco sin explicación.
5. Si el usuario no tiene token en `localStorage`, redirigí a `index.html` antes de pintar nada.

### JavaScript

- `assets/js/rotulos.js` consume el CRUD de rótulos ya implementado en `apps.labels`
  (`http://127.0.0.1:8000/api/v1/labels/...`). Reutilizá el módulo `frontend/pedidos/api.js` si te
  sirve tal cual; si no, replicá las llamadas ahí — pero **no dupliques dos fuentes de verdad**:
  elegí una y usala.
- El wrapper `apiFetch` es el mismo que el resto del frontend (ver `pedidos.js` / `ayuda.js`):
  header `Authorization: Bearer`, en 401 limpiar `access`/`refresh`/`user` y volver a `index.html`,
  y en 403 con `must_change_password` redirigir a `cambiar-password.html`.
- El botón de logout hace `POST` al endpoint de logout con el refresh y después limpia la sesión,
  igual que en las otras páginas.
- La búsqueda se resuelve contra el backend con `?search=` (el listado ya lo soporta), con un
  debounce corto — no filtres en memoria.

### Cableado

- En `backend/apps/accounts/dashboard_views.py`, el ítem `labels` del menú tiene que apuntar a
  `rotulos.html` (hoy va a `plantillas_rotulos.html`).
- En `frontend/pedidos/diseñorotulos.html`, después de guardar el rótulo la redirección debe ir a
  `../rotulos.html` (hoy va a `dashboard.html`), y agregá un enlace "Volver a mis rótulos" en la
  barra del editor.
- **No borres** `plantillas_rotulos.html`, `assets/js/dashboard_rotulos.js` ni
  `assets/css/gestionrotulos.css`: quedan sin uso, pero la decisión de eliminarlos es del dueño
  del proyecto.

### Editor: solo el marco

En `pedidos/diseñorotulos.html`, reemplazá **únicamente el encabezado** — sacá `<app-sidebar>` y
el `<script src="../assets/html/aside.js">`, y poné el mismo `header.topbar` que las demás páginas
(avatar, nombre, email, "Volver a mis rótulos", "Cerrar sesión"), enlazando `../assets/css/rotulos.css`
para que herede los estilos comunes.

**No toques nada del canvas del editor**: ni el arrastre de campos, ni las posiciones en %, ni el
tamaño en cm, ni el logo, ni el QR, ni el export a PNG/PDF, ni el payload que arma `#saveBtn`.
Eso funciona y es lo único terminado del módulo — el cambio es puramente de encabezado.
