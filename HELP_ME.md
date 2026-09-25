# HELP ME

Cómo hacer las cosas que uno se olvida. Notas de uso, no de arquitectura: lo
técnico del código vive en `CLAUDE.md`.

- [Levantar todo para probar](#levantar-todo-para-probar)
- [Conectar una tienda de Tiendanube](#conectar-una-tienda-de-tiendanube)
- [Conectar un cliente que NO tiene Tiendanube](#conectar-un-cliente-que-no-tiene-tiendanube)

---

## Levantar todo para probar

Son **tres** procesos, y si falta uno parece que la app está rota.

**1. El backend** (`http://127.0.0.1:8000`):

```bash
cd backend && source venv/bin/activate && python manage.py runserver
```

**2. El frontend** (`http://localhost:8001`). Con el botón *Go Live* de Live
Server en VS Code: `.vscode/settings.json` ya lo fija en el puerto 8001 y con
`frontend/` como raíz, que es lo que el backend espera en `FRONTEND_URL`. Sin
VS Code:

```bash
cd frontend && python3 -m http.server 8001
```

> **El puerto importa.** Cuando Tiendanube termina de autorizar una tienda,
> manda el navegador a `FRONTEND_URL/tiendas.html` (`backend/.env`). Si ahí no
> hay nada escuchando, la tienda se conecta igual pero vos caés en una página
> muerta — y si era una instalación sin sesión, el token para reclamar la tienda
> se pierde y queda sin dueño.

**3. El worker de integraciones:**

```bash
cd backend && source venv/bin/activate && python manage.py run_integrations_worker
```

> **Sin esto los pedidos no entran nunca.** Conectar una tienda solo deja
> eventos anotados en la base (`IntegrationEvent`); quien los ejecuta —registrar
> webhooks, importar los pedidos que ya existían— es el worker. Si conectaste una
> tienda y no aparece ni un pedido, esto es lo primero que hay que mirar.

---

## Conectar una tienda de Tiendanube

La pantalla es **`tiendas.html`** (menú *Tiendas*), no `integraciones.html`.

1. Levantá los tres procesos de arriba.
2. Entrá a `tiendas.html` y tocá **Conectar con Tiendanube**.
3. Autorizás en Tiendanube y volvés solo.
4. Los pedidos se ven en `pedidos.html`, filtrando por tienda.

### "No me dejó elegir qué tienda conectar"

Es el comportamiento normal, no un error. La URL de autorización no lleva ninguna
referencia a la tienda:

```
https://www.tiendanube.com/apps/42481/authorize?state=<firmado>
```

Tiendanube resuelve **de qué tienda se trata a partir de la sesión de tu
navegador**. Si ya estabas logueado y la app ya estaba autorizada ahí, no tiene
nada que preguntarte: te devuelve el código y volvés conectado. No hay ningún
parámetro para forzar que pregunte — está verificado contra su documentación.

**La solución son los perfiles de navegador**, no incógnito.

Un perfil de Chrome es una sesión aparte y permanente, con sus propias cookies.
Creás uno por tienda (avatar arriba a la derecha → *Agregar*), te logueás una vez
y queda. Después cambiás de perfil con un click. A diferencia de incógnito, no se
te borra la sesión al cerrar la ventana.

En Firefox, la extensión **Multi-Account Containers** hace lo mismo sin cambiar
de ventana: click derecho en el link → abrir en el contenedor que quieras.

Así se pueden tener varias tiendas conectadas a la vez sin pisarse: cada
`StoreConnection` es única por plataforma + id de tienda.

### Conectar la tienda demo de otra persona

La tienda **no está en la computadora de esa persona** — vive en los servidores
de Tiendanube; ella solo tiene las credenciales del admin. Así que no hace falta
desplegar nada ni tocar su máquina: alcanza con hacerlo desde acá, en un perfil
aparte, entrando con esas credenciales en el paso 3.

Desplegar solo hace falta si esa persona tiene que apretar el botón **desde su
propia computadora**: ahí `FRONTEND_URL` y `API_BASE` apuntan a `localhost`, que
para ella es su propia máquina.

---

## Conectar un cliente que NO tiene Tiendanube

`integraciones.html` es el camino para un cliente que no tiene tienda en ninguna
plataforma: tiene su propio sistema (un ERP, una web a medida, un Excel que
alguien automatizó) y aun así quiere que sus pedidos lleguen a la app.

No tiene nada que ver con `tiendas.html`. Ahí se conectan tiendas de Tiendanube
por OAuth; acá le damos a un sistema ajeno una puerta para empujar pedidos.

Es una pantalla de **admin** (permiso `integrations.manage`). Un comerciante no
la ve.

Hay dos caminos y conviene elegir uno solo por cliente.

---

### Camino A — Clave de API (el más simple)

Usalo cuando el otro lado puede escribir el pedido con **nuestros** nombres de
campo. Es una llamada HTTP y nada más.

#### 1. Crear la clave

1. Entrá como admin y abrí **Integraciones** en el menú.
2. Pestaña **Claves de API** → completá:
   - **Nombre**: cómo vas a reconocerla después (ej. `ERP de Distribuidora Sur`).
   - **Dueño**: el usuario de la app al que van a pertenecer esos pedidos. Esto
     es importante: los pedidos que entren con esta clave son de ese usuario, no
     de quien creó la clave.
3. Guardá.

> **La clave completa se muestra una sola vez, en ese momento.** Después ni un
> admin la puede volver a ver: solo revocarla y generar otra. Copiala y pasásela
> al cliente por un canal seguro antes de cerrar la pantalla.

#### 2. Decirle al cliente a dónde pegarle

```
POST http://127.0.0.1:8000/api/v1/ingest/orders/
Api-Key: <la clave que copiaste>
Content-Type: application/json
```

(En producción, el mismo path sobre el dominio real.)

#### 3. El formato del pedido

Por este camino **no hay mapeo**: el JSON tiene que venir ya con nuestros nombres
de campo, exactamente así.

| Campo | ¿Obligatorio? |
|---|---|
| `destinatario` | sí |
| `domicilio` | sí |
| `ciudad` | sí |
| `numero` | no |
| `provincia` | no |
| `cp` | no |
| `referencia` | no |
| `descripcion` | no |
| `external_id` | no, pero ver abajo |

Un pedido:

```json
{
  "destinatario": "María Fernanda Gutiérrez",
  "domicilio": "Av. Cabildo",
  "numero": "4781",
  "ciudad": "Buenos Aires",
  "provincia": "CABA",
  "cp": "1602",
  "descripcion": "Caja x2",
  "external_id": "ERP-10453"
}
```

Varios de una vez: mandá un array de esos objetos. La respuesta es
`{"results": [...]}` con un resultado por pedido, así una fila rota no tumba al
resto.

**Poné siempre `external_id`.** Es lo que hace la llamada idempotente: si ese
usuario ya tiene un pedido con ese `external_id`, se devuelve el que ya existe en
vez de crear uno duplicado. Sin `external_id`, reintentar la misma llamada crea
un pedido nuevo cada vez.

#### 4. Probarlo

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ingest/orders/ \
  -H "Api-Key: PEGA_ACA_LA_CLAVE" \
  -H "Content-Type: application/json" \
  -d '{"destinatario":"Prueba","domicilio":"Calle Falsa","numero":"123","ciudad":"Buenos Aires","cp":"1602","external_id":"PRUEBA-1"}'
```

Después entrá a `pedidos.html` **con el usuario que pusiste como dueño** y
buscá el pedido: aparece con origen `api`. Si corrés el mismo comando dos veces,
tiene que seguir habiendo uno solo.

---

### Camino B — Webhook entrante (cuando el otro lado no se adapta)

Usalo cuando el sistema del cliente ya manda un JSON con **sus** nombres de campo
y no lo va a cambiar. Acá nosotros traducimos.

#### 1. Crear el webhook

1. Pestaña **Webhooks entrantes** → completá:
   - **Nombre**: para reconocerlo.
   - **Dueño**: igual que antes, de quién son los pedidos.
   - **Slug**: la parte final de la URL. La URL queda
     `/api/v1/ingest/webhooks/<slug>/`.
   - **Mapeo**: un JSON que dice *nuestro campo → la clave de ellos*.
2. Guardá. El **secreto** lo genera el servidor y queda visible en la tabla.

#### 2. El mapeo

Se lee al revés de lo que uno espera: la izquierda es **nuestra**, la derecha es
**de ellos**. Si su JSON es así:

```json
{ "cliente": "María Gutiérrez", "calle": "Av. Cabildo 4781", "loc": "CABA", "postal": "1602", "nro_pedido": "A-88" }
```

el mapeo es:

```json
{
  "destinatario": "cliente",
  "domicilio": "calle",
  "ciudad": "loc",
  "cp": "postal",
  "external_id": "nro_pedido"
}
```

Las claves de la izquierda salen de la tabla del Camino A, y las obligatorias son
las mismas tres.

#### 3. Decirle al cliente cómo firmar

```
POST http://127.0.0.1:8000/api/v1/ingest/webhooks/<slug>/
X-Webhook-Signature: <HMAC-SHA256 del cuerpo crudo, con el secreto, en hex>
Content-Type: application/json
```

Esa firma es **la única** verificación: no hay login del otro lado. Se comprueba
antes de tocar nada, y una firma inválida devuelve 401 sin crear ningún pedido.
El secreto no viaja nunca en la request — solo se usa para calcular la firma.

En Python, del lado de ellos:

```python
import hmac, hashlib, json, requests

secreto = "EL_SECRETO_DE_LA_TABLA"
cuerpo = json.dumps({"cliente": "Prueba", "calle": "Falsa 123", "loc": "CABA", "postal": "1602", "nro_pedido": "A-88"}).encode()
firma = hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()

requests.post(
    "http://127.0.0.1:8000/api/v1/ingest/webhooks/MI-SLUG/",
    data=cuerpo,
    headers={"X-Webhook-Signature": firma, "Content-Type": "application/json"},
)
```

La firma se calcula sobre **los bytes exactos** que se envían. Si se serializa el
JSON dos veces (una para firmar y otra para mandar) y queda distinto un espacio,
la firma no valida.

#### 4. Probarlo

Igual que antes: `pedidos.html` con el usuario dueño. Los pedidos entran con
origen `webhook`. Además, cada llamada queda en la auditoría con el payload
crudo (`order.webhook_ingest`), que es lo que conviene mirar cuando el cliente
dice "yo lo mandé" y no aparece.

---

### Avisos salientes (la otra dirección)

La pestaña **Webhooks salientes** es lo inverso: cuando un pedido cambia de
estado, nosotros le avisamos al sistema del cliente. Se carga la URL de ellos y
el servidor genera el secreto. **Entregas** es el log de esos avisos, con lo que
respondió el otro lado — es el primer lugar donde mirar si el cliente dice que no
le llegan los avisos.

Es independiente de A y B: podés tener entrada por API y salida por webhook.

---

### Cómo elegir

- ¿El cliente puede adaptar su JSON a nuestros nombres? → **Camino A**. Menos
  piezas, menos cosas que romper.
- ¿No puede o no quiere? → **Camino B**.
- ¿El cliente tiene Tiendanube? → **ninguno de los dos**: eso es `tiendas.html`.
