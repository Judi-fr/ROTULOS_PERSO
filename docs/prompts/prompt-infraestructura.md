# Prompt para Claude Code — CI, imagen Docker y logging/errores centralizados

ROTULOS_PERSO (Buspack). Respetá `CLAUDE.md`. **No `git pull/push/commit`.**
Escribí el código directo: no hace falta auditar el proyecto.

Cubre las stories 1 a 4 del backlog. Lo que ya existe y **no hay que rehacer**: `Dockerfile`
(gunicorn, usuario sin privilegios), `docker-compose.yml` (Postgres 17 con healthcheck),
`config/settings/prod.py` (HSTS, cookies seguras, `SECURE_PROXY_SSL_HEADER`, sin fallback a
SQLite) y el endpoint `/api/v1/health/`.

Lo que falta es la automatización y la observabilidad.

---

## 1. CI del backend — `.github/workflows/backend.yml`

Corre en cada push y en cada pull request, sobre `ubuntu-latest` con Python 3.13:

- Instala `backend/requirements.txt` (con caché de pip).
- `python manage.py check`.
- **`python manage.py makemigrations --check --dry-run`** — falla si alguien cambió un modelo y no
  generó la migración. En este proyecto, con tantas migraciones de siembra de permisos, es el error
  más probable y el más molesto de descubrir tarde.
- `python manage.py test`.

Las variables que el settings necesita (`SECRET_KEY`, `DEBUG`, `DATABASE_URL`, `ALLOWED_HOSTS`,
`CORS_ALLOWED_ORIGINS`, `GOOGLE_CLIENT_ID`, las de email) van como `env:` del job con valores de
prueba — **nunca secretos reales**. `SECRET_KEY` de al menos 32 caracteres, así de paso desaparece
el `InsecureKeyLengthWarning` que ensucia toda la salida de los tests.

Los tests corren contra SQLite en memoria, como ya lo hacen hoy: no levantes Postgres en CI.

## 2. CI del frontend — `.github/workflows/frontend.yml`

El frontend no tiene build step ni dependencias, así que el pipeline es una verificación honesta,
no un build inventado:

- `node --check` sobre cada `.js` de `frontend/assets/js/` y `frontend/pedidos/` (es lo que ya se
  usó a mano para validarlos).
- Un chequeo de que no quedaron URLs hardcodeadas: que **no aparezca `127.0.0.1:8000`** en ningún
  archivo fuera de `assets/js/config.js` (excluí `assets/apis/vendor/`, `index_test.html` y los
  archivos viejos de Google/OAuth).

**Excluí `frontend/assets/apis/vendor/` de todo**: son dependencias de terceros, no código tuyo.

## 3. Imagen Docker — `.github/workflows/release.yml`

Solo en push a la rama principal y en tags:

- Construye la imagen con el `Dockerfile` existente.
- La publica en **GitHub Container Registry** (`ghcr.io`), etiquetada con el SHA del commit y con
  `latest`. Usá el `GITHUB_TOKEN` que ya provee Actions, sin secretos nuevos.

**No inventes un paso de despliegue**: todavía no hay servidor destino. Dejá un job `deploy`
comentado, con el esqueleto de lo que haría (entrar al servidor, hacer pull de la imagen, levantar
`docker compose`) y una nota diciendo que se completa cuando exista el entorno. Un pipeline que
finge desplegar es peor que no tenerlo.

Agregá también un `config/settings/stage.py` que herede de `prod.py` y solo relaje lo que tenga
sentido en un entorno de pruebas (por ejemplo `SECURE_SSL_REDIRECT` y HSTS apagados si stage no
tiene certificado). Hoy solo existen `dev` y `prod`.

---

## 4. Logging estructurado y errores centralizados

Esta es la parte que mejora el producto, no solo el proceso.

### 4.1 Logging en `base.py`

Hoy el bloque `LOGGING` vive **solo en `prod.py`** y es texto plano a consola. Movelo a `base.py`
para que aplique en todos los entornos, y hacelo estructurado:

- Un formatter propio que emite **una línea JSON por evento** con: `timestamp` (ISO 8601),
  `level`, `logger`, `message`, `module`, y `request_id` cuando haya. Escribilo a mano con
  `logging.Formatter` y `json.dumps` — **no agregues ninguna librería**.
- `LOG_LEVEL` y `LOG_FORMAT` (`json` o `plain`) leídos del `.env`. En desarrollo conviene `plain`,
  que es legible en la terminal; en producción `json`, que lo parsea cualquier agregador. Default:
  `plain` en `dev.py`, `json` en `prod.py`.
- Logger `django.request` en WARNING y el root en el nivel de `LOG_LEVEL`.
- Sumá `LOG_LEVEL` y `LOG_FORMAT` a `.env.example`.

En `prod.py`, borrá el `LOGGING` que quedó duplicado.

### 4.2 Request ID

Middleware nuevo que, en cada request, toma el header `X-Request-ID` si viene (lo puede mandar el
proxy) o genera un UUID4, lo guarda en un `contextvar` para que el formatter lo incluya en cada
línea de log, y lo devuelve en la respuesta con el mismo header.

Sin esto, "estructurado" no sirve de mucho: es lo que te deja seguir una llamada entera entre
varias líneas de log.

### 4.3 Manejo centralizado de errores

Hoy `REST_FRAMEWORK` **no tiene `EXCEPTION_HANDLER`**, así que un error no controlado devuelve un
500 con formato distinto al de los errores de validación, y un cliente de la API no puede
parsearlo.

Escribí un handler propio y registralo en `REST_FRAMEWORK["EXCEPTION_HANDLER"]`:

- Delega primero en el handler por defecto de DRF.
- **Muy importante: no cambies el cuerpo de los errores que DRF ya devuelve.** El frontend parsea
  `{"detail": "..."}` y los diccionarios de errores por campo (`{"email": ["..."]}`) en todas las
  pantallas; si cambiás ese shape rompés login, registro, pedidos y el CRUD de usuarios de una.
  Lo único que agregás a esas respuestas es el campo `request_id`.
- Cuando el handler por defecto devuelve `None` (excepción no controlada, o sea un 500), respondé
  un JSON con la misma forma que el resto: `{"detail": "Error interno del servidor.",
  "request_id": "..."}`, y **logueá la excepción completa con traceback** con `logger.exception`.
  Nunca expongas el traceback ni el mensaje interno en la respuesta.

## Test

Uno solo: que un error de validación conocido (por ejemplo un registro con un email ya usado)
siga devolviendo **exactamente el mismo cuerpo que hoy**, con el `request_id` agregado y nada más.
Es lo único de esta tarea que puede romper el frontend entero en silencio.
