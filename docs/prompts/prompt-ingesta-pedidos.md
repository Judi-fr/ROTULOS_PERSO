# Prompt para Claude Code — Ingesta de pedidos (stories 20 a 24)

ROTULOS_PERSO (Buspack — paquetería liviana; un pedido es un envío/encomienda que viaja en micro).
Respetá `CLAUDE.md`. **No `git pull/push/commit`.** Escribí el código directo: no hace falta
auditar el proyecto.

Es la contracara del motor de rótulos que ya existe: hoy los pedidos solo se cargan de a uno desde
`pedidos.html`, pensado para el cliente final eligiendo entre **sus** direcciones guardadas. Falta
la carga operativa y las entradas automáticas.

**Se puede correr en dos partes**: la Parte A (20, 21, 22) es autónoma y ya sirve sola; la Parte B
(23, 24) se apoya en ella.

---

## Base común — `external_id`

Antes que nada, agregá a `Order` el campo **`external_id`** (`CharField`, blank, indexado, único
por usuario) y `source` (`TextChoices`: `web`, `manual`, `import`, `api`, `webhook`; default `web`).

`external_id` es el identificador que el pedido tiene **en el sistema del cliente** (su ERP, su
tienda). Es la pieza que sostiene todo lo demás: sin él, reimportar el mismo CSV duplica todo y un
reintento de la API crea el pedido dos veces. Con él, cada entrada puede ser idempotente.

`source` es para saber por dónde entró cada pedido — sirve para los reportes y para depurar.

---

# Parte A — Carga manual y por archivo

## A.1 Alta manual de un envío (story 20)

Lo que hay hoy es self-service: el cliente elige una de sus direcciones. Lo que falta es la carga
operativa: alguien de Buspack da de alta un envío escribiendo los datos del destinatario, que no
está guardado en ningún lado.

- Permiso nuevo **`orders.create_manual`** (admin y operator; no subscriber), sembrado por
  migración idempotente.
- Endpoint que crea **dirección y pedido en una sola llamada**: recibe los datos del destinatario
  (nombre, domicilio, número, ciudad, provincia, CP, referencia) más la descripción, crea la
  `Address` y el `Order` en una transacción, y devuelve el pedido.
- Si `orders.create_for_others` (otro permiso nuevo, solo admin) está presente, acepta además un
  `user_id` para dar de alta el envío **a nombre de otro usuario**. Sin ese permiso, el pedido es
  siempre del usuario autenticado — nunca aceptes un `user_id` de quien no lo tiene.
- Pantalla: agregá la sección de alta manual al panel admin (`gestionuser.html`) o una página
  propia con el mismo patrón visual que `pedidos.html`; elegí lo que quede más limpio.

## A.2 Importación desde CSV/Excel (story 21)

Dependencia nueva: **`openpyxl`** en `requirements.txt` (Python puro, sin librerías de sistema)
para leer `.xlsx`. El CSV se lee con el módulo `csv` de la stdlib.

Modelo **`OrderImport`** en `apps.orders`:

- `user`, `file` (`FileField`), `original_filename`, `status` (`pending`, `mapping`, `processing`,
  `done`, `failed`), `total_rows`, `imported_count`, `skipped_count`, `error_count`,
  `errors` (`JSONField`: lista de `{fila, columna, mensaje}`), `mapping` (`JSONField`),
  `created_at`/`updated_at`.

Flujo en tres pasos, cada uno su endpoint:

1. **Subir** → guarda el archivo, detecta el delimitador y la codificación (probá UTF-8 y
   Latin-1: los Excel exportados en Argentina suelen venir en Latin-1 con `;` como separador),
   lee los encabezados y devuelve las **primeras 10 filas** como vista previa.
2. **Mapear** → recibe el mapeo de columnas y valida sin escribir nada: devuelve cuántas filas
   están bien y el detalle de las que fallan, **con número de fila**.
3. **Confirmar** → crea los pedidos dentro de una transacción por fila.

Reglas que importan:

- **Una fila con error no aborta la importación**: se saltea, se cuenta y se detalla. Igual criterio
  que el lote de rótulos.
- Si la fila trae un `external_id` que ya existe para ese usuario, **se saltea** (no se duplica ni
  se pisa) y se cuenta como `skipped`.
- Tope de filas por archivo (`ORDERS_IMPORT_MAX_ROWS`, default 1000) y de tamaño de archivo.
- Al terminar, el `OrderImport` queda con los contadores y la lista de errores, para poder mostrar
  "342 importados, 6 salteados, 2 con error" y decir cuáles.

## A.3 Mapeo de columnas y plantilla de importación (story 22)

Nadie exporta su planilla con los nombres de campo de tu sistema. El mapeo es la traducción entre
las columnas del archivo y los campos del pedido:

- Los campos destino son: destinatario, domicilio, número, ciudad, provincia, CP, referencia,
  descripción y `external_id`. Marcá cuáles son obligatorios.
- **Auto-detección**: si un encabezado coincide (ignorando mayúsculas, acentos y espacios) con un
  nombre conocido —"destinatario", "nombre", "direccion", "calle", "localidad", "codigo postal",
  "cp", "provincia"—, proponelo mapeado de entrada. Ahorra el 90% del trabajo.
- Modelo **`ImportMapping`**: `user`, `name`, `mapping` (`JSONField`), `created_at`. Es la
  "plantilla de importación" de la story: se guarda un mapeo con nombre y la próxima vez que llegue
  el mismo formato de archivo se aplica de una.
- Y una **plantilla descargable**: un endpoint que devuelve un CSV de ejemplo con los encabezados
  correctos y una fila de muestra, para el cliente que prefiere adaptarse a tu formato.

Permisos nuevos `orders.import` (admin y operator) y `orders.import_mappings` para guardar
plantillas propias.

Pantalla `frontend/importar.html` + su CSS y JS, con el patrón visual de `documentos.html`: subir
archivo → tabla de vista previa con un `<select>` de campo por columna → resumen de validación →
confirmar. Enlazada desde el menú del dashboard.

---

# Parte B — Entradas automáticas

## B.1 API de ingesta (story 23)

Un ERP no se loguea con usuario y contraseña: necesita una credencial de máquina.

- Modelo **`IntegrationKey`**: `owner` (el usuario dueño de los pedidos que entren por ahí),
  `name`, `key_hash`, `prefix` (los primeros caracteres, para poder identificarla en pantalla),
  `is_active`, `last_used_at`, `created_at`. **Guardá solo el hash**, y mostrá la clave completa
  una única vez al crearla.
- Clase de autenticación DRF propia que lee el header `Authorization: Api-Key <clave>`, busca por
  prefijo, compara el hash y deja el `owner` como `request.user`. **No toques la autenticación JWT
  existente**: esta convive con aquella, solo para los endpoints de ingesta.
- `POST /api/v1/ingest/orders/` acepta **un pedido o una lista**, con los mismos campos que la
  importación. Idempotente por `external_id`: si ya existe, devuelve el pedido existente con
  `created: false` en vez de crear otro. Responde por ítem, así el cliente sabe cuáles entraron.
- Rate limiting con el throttling de DRF (`ScopedRateThrottle`), configurable por `.env`.
- ABM de claves en el panel admin, permiso `integrations.manage` (solo admin).

## B.2 Webhooks (story 24)

Dos direcciones, y las dos hacen falta:

**Entrante** — `POST /api/v1/ingest/webhooks/<slug>/`: recibe el payload de una tienda o ERP y crea
pedidos. Cada webhook configurado tiene su `IntegrationKey`, un `secret` y un mapeo de campos
(reusá el `mapping` de la importación: el problema es el mismo, traducir campos ajenos a los
propios). **Verificá la firma** HMAC-SHA256 del header contra el secreto antes de procesar nada, y
respondé 401 si no coincide. Guardá el payload crudo recibido para poder depurar.

**Saliente** — cuando un pedido cambia de estado, notificar al sistema del cliente: modelo
`WebhookEndpoint` (`owner`, `url`, `secret`, `events`, `is_active`) y un envío firmado con
HMAC-SHA256. Sin cola de tareas, hacelo síncrono con timeout corto (3 segundos) y **que un fallo
de entrega nunca rompa la operación que lo disparó**: si el cliente no responde, se registra el
intento fallido y el pedido sigue su curso. Registrá cada envío (`WebhookDelivery`: endpoint,
evento, status HTTP, intento, respuesta) para que el reintento se pueda agregar después.

---

## Tests

Tres, todos sobre lo que se rompe en silencio:

1. Importar dos veces el mismo archivo con `external_id` no duplica pedidos: la segunda vez todos
   quedan como salteados.
2. Un CSV con una fila inválida importa el resto y reporta esa fila con su número.
3. Un webhook entrante con firma incorrecta devuelve 401 y **no crea ningún pedido**.
