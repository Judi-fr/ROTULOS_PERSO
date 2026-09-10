# Prompt para Claude Code — Plantillas de rótulos + impresión

ROTULOS_PERSO (Buspack — paquetería liviana; un rótulo es la etiqueta de encomienda que se pega al
paquete). Respetá `CLAUDE.md`. **No `git pull/push/commit`.** Escribí el código directo: no hace
falta auditar el proyecto.

## El problema

`LabelTemplate` existe como modelo y tiene endpoints, pero **la tabla está vacía y nada en el
producto crea plantillas**: el editor guarda `Label`, no `LabelTemplate`, y los endpoints de
plantillas exigen `labels.manage_templates` (solo admin) sin ninguna pantalla detrás.

Consecuencia: la generación por lote pide `template_id` y no hay ninguno, así que el lote por
`order_ids` o por `filters` es imposible de usar. Esta tarea lo arregla y agrega la impresión.

---

# Parte 1 — Plantillas

## 1.1 Modelo

Agregá a `LabelTemplate` el campo **`is_active`** (`BooleanField`, default `True`) si no lo tiene:
archivar una plantilla es soft-delete, igual que en `Label` y `Document`. Los listados devuelven
solo las activas.

## 1.2 Plantilla inicial por migración

Migración idempotente en `apps.labels` que siembra **una plantilla pública de Buspack** llamada
`"Etiqueta de encomienda Buspack"`, `owner=None`, `is_public=True`, 10 × 15 cm, con el diseño
estándar: logo arriba a la izquierda, QR arriba a la derecha con `{{tracking_url}}`, los campos de
texto con marcadores (`Remitente: {{remitente}}`, `Destinatario: {{destinatario}}`,
`Domicilio: {{domicilio}}`, `CP: {{cp}}`, `{{localidad}}`, `{{pedido}}`) y el código de barras
abajo con `{{tracking}}`.

Es el desbloqueo inmediato: con eso el lote funciona apenas corra la migración.

## 1.3 Permisos

Hoy crear una plantilla exige `labels.manage_templates`, que es solo admin. Partilo en dos:

- **`labels.template_create`** (nuevo, los cuatro roles canónicos): crear y editar plantillas
  **propias** (`owner=request.user`, `is_public=False`).
- **`labels.manage_templates`** (existente, solo admin): tocar plantillas **públicas** o de otro
  usuario, y marcar una como pública.

Un usuario ve en el listado: las públicas + las propias. Nunca las privadas de otro.

Sembrá el permiso nuevo con una migración idempotente, con el mismo patrón que las anteriores.

## 1.4 "Guardar como plantilla" en el editor

En `frontend/pedidos/diseñorotulos.html`, un botón **"Guardar como plantilla"** al lado de los de
guardar/exportar: pide un nombre y hace `POST` a plantillas con el diseño y las medidas actuales
del canvas. Queda como plantilla propia (privada).

Es el puente que faltaba: se diseña un rótulo y ese diseño pasa a ser reutilizable.

## 1.5 Vincular el rótulo con su plantilla

`Label.template` existe pero **no lo escribe nadie**. Cuando un rótulo se crea a partir de una
plantilla (desde el editor abierto con una plantilla, o desde el lote), guardá esa FK. Sin eso no
hay forma de saber qué rótulos salieron de qué plantilla.

## 1.6 Pantalla de plantillas

`frontend/plantillas.html` + `assets/css/plantillas.css` + `assets/js/plantillas.js`, con la misma
estructura visual que `rotulos.html` / `documentos.html` (`div.layout` → `header.topbar` con
avatar, "Volver al panel" y logout → `main.main` con `.main-heading` y `profile-card`; fuente Karla
por CDN, sin Bootstrap ni MDI; `config.js` + `auth.js` + su script, clásicos).

Grilla de tarjetas con: nombre, medidas, si es pública o propia, y una **vista previa real** —
pedile el PDF al endpoint de render con datos de ejemplo y mostralo, o usá el campo `preview` si
está cargado. Acciones por tarjeta:

- **Usar** → abre el editor con esa plantilla cargada (`diseñorotulos.html?template=<id>`).
- **Duplicar** → copia propia con `" (copia)"` en el nombre.
- **Editar** → solo si es propia (o admin).
- **Archivar** → soft-delete, con confirmación.

Buscador contra el backend (`?search=`), y estados de carga y vacío explícitos.

Enlazala desde el menú del dashboard: en `dashboard_views.py`, un ítem nuevo
`{"key": "templates", "label": "Plantillas", "url": "plantillas.html", "enabled": True}`, y agregá
su ícono y grupo en `assets/js/dashboard.js` junto a los que ya están.

## 1.7 Lote sin plantilla

En `POST /api/v1/labels/batch/`, cuando no se manda `template_id` con `order_ids`/`filters`, usá
**la plantilla pública por defecto** en vez de devolver 400. Si no hubiera ninguna, ahí sí un 400
con un mensaje que diga qué hacer.

Y agregá al panel de lote de `rotulos.html` un selector de plantilla poblado desde el listado.

## 1.8 Lote duplicado

El lote por `filters` toma los pedidos del rango sin mirar si ya tienen rótulo generado, así que
correrlo dos veces imprime todo de nuevo sin avisar. Agregá un flag opcional
`skip_existing` (default `false`): cuando es `true`, saltea los pedidos que ya tienen un `Label`
asociado, y contá cuántos se saltearon en el `error_message` del documento, igual que con los
omitidos por error.

---

# Parte 2 — Impresión

## 2.1 Botón "Imprimir"

En tres lugares, con el mismo helper compartido (ponelo en `assets/js/auth.js` o en un
`assets/js/print.js` nuevo, pero **una sola implementación**):

- Editor (`diseñorotulos.html`): imprime el rótulo actual, ya guardado.
- `rotulos.html`: en cada tarjeta.
- `documentos.html`: en cada documento `ready` (solo si es PDF; un ZIP no se imprime — ahí
  mostrá solo Descargar).

## 2.2 Cómo imprimir

El endpoint del PDF exige `Authorization`, así que no se puede apuntar una ventana directo a la
URL. El flujo es:

1. Pedir el PDF con `apiFetch` → `blob` → `URL.createObjectURL`.
2. Cargarlo en un **`<iframe>` oculto** agregado al documento.
3. En el `onload` del iframe, llamar `iframe.contentWindow.print()`.
4. Liberar el object URL y sacar el iframe cuando termina.

Si el iframe falla (algunos navegadores no imprimen PDF embebido), **fallback**: abrir el blob en
una pestaña nueva para que el usuario imprima desde el visor. Mostrá un mensaje claro si el
navegador bloquea la ventana emergente, en vez de quedarte en silencio.

Botón deshabilitado mientras se genera el PDF, con un texto tipo "Preparando…".

## 2.3 Layout de impresión para hoja A4

Un rótulo de 10 × 15 cm impreso en una hoja A4 desperdicia media hoja, y para una demo o una
oficina sin impresora térmica eso es justamente lo que se va a usar. Agregá al lote un parámetro
`page_layout`:

- `"label"` (default): lo actual, una página del tamaño exacto del rótulo — es lo correcto para
  impresora de etiquetas.
- `"a4"`: rótulos acomodados en grilla sobre hojas A4 (21 × 29.7 cm), con un margen de 1 cm,
  0.5 cm de separación entre rótulos y **líneas de corte punteadas** finas alrededor de cada uno.
  La cantidad por hoja se calcula a partir del tamaño del rótulo: con 10 × 15 cm entran 2 por hoja;
  con uno más chico, más. Los rótulos que no entran siguen en la hoja siguiente.

La grilla se dibuja con las mismas funciones de `rendering.py` — un rótulo se dibuja igual, solo
cambia dónde se posiciona en la página. **No dupliques la lógica de dibujo.**

Sumá el selector de layout al panel de lote de `rotulos.html`.

## Test

Uno solo: el cálculo de cuántos rótulos entran por hoja A4 y en qué posición va cada uno —
que con 10 × 15 cm den 2 por hoja y que ninguno se salga de los márgenes. Es matemática que si
sale mal recorta etiquetas y no se nota hasta que salen impresas.
