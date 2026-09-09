# Prompt para Claude Code — Refresh token, config central de la API y settings de producción

ROTULOS_PERSO (Buspack). Respetá `CLAUDE.md`. **No `git pull/push/commit`.** Escribí el código
directo: no hace falta auditar el proyecto ni escribir tests para esta tarea.

Son cuatro arreglos independientes. Hacelos todos.

---

## 1. Usar el refresh token (lo más importante)

El backend expone `POST /api/v1/auth/refresh/` (SimpleJWT, con rotación activada: devuelve
`access` **y** `refresh` nuevos). El frontend **nunca lo llama**: cada wrapper `apiFetch` trata
cualquier 401 como sesión terminada, limpia `localStorage` y manda a `index.html`. Resultado: al
usuario lo expulsan aunque tenga un refresh válido.

El `apiFetch` está duplicado en: `assets/js/pedidos.js`, `assets/js/perfil.js`,
`assets/js/ayuda.js`, `assets/js/dashboard.js`, `assets/js/admingestion_test.js`,
`pedidos/api.js` y `assets/js/rotulos.js` (si ya existe).

Creá **`frontend/assets/js/auth.js`** con la lógica de sesión compartida y hacé que todos esos
archivos la usen en vez de tener su propia copia:

- `getAccessToken()`, `getRefreshToken()`, `getCurrentUser()`, `clearSession()`, `logout()`.
- `apiFetch(url, options)`:
  1. Agrega `Authorization: Bearer <access>`.
  2. Si la respuesta es **401**, intenta **una sola vez** `POST /auth/refresh/` con el refresh de
     `localStorage`. Si sale bien, guarda el `access` (y el `refresh` nuevo, que la rotación
     devuelve) y **reintenta la request original una vez**. Si el refresh falla o no hay refresh,
     recién ahí limpia la sesión y redirige a `index.html`.
  3. Si la respuesta es **403** con `must_change_password`, redirige a `cambiar-password.html`
     (comportamiento actual, no lo cambies).
- **Una sola renovación concurrente**: si varias llamadas reciben 401 a la vez, todas tienen que
  esperar la misma promesa de refresh, no disparar una cada una. Guardá la promesa en curso en una
  variable del módulo y reutilizala.
- No entres en bucle: un 401 en la propia llamada de refresh cierra sesión, nunca reintenta.

`auth.js` se carga como script clásico antes que los demás y expone lo suyo en `window` (por
ejemplo `window.Auth`), porque casi todas las páginas usan scripts clásicos. `pedidos/api.js`, que
es un módulo ES, puede leer `window.Auth` igual — no conviertas el resto de las páginas a módulos.

Agregá `<script src="assets/js/auth.js"></script>` antes del script propio de cada página
(`../assets/js/auth.js` en las que están dentro de `pedidos/`).

---

## 2. Config central de la API

`http://127.0.0.1:8000` está escrito a mano en todos los JS del frontend. Creá
**`frontend/assets/js/config.js`**, cargado antes de todo lo demás:

```js
window.APP_CONFIG = {
  API_BASE: "http://127.0.0.1:8000/api/v1",
};
```

Reemplazá **todas** las URLs hardcodeadas del frontend por rutas armadas sobre
`window.APP_CONFIG.API_BASE`. Que no quede ni una ocurrencia de `127.0.0.1:8000` fuera de
`config.js` (no toques `assets/apis/` ni `index_test.html` ni los archivos de Google/OAuth viejos).

---

## 3. `settings/prod.py`

No existe. Creá `backend/config/settings/prod.py` importando de `base` y con lo mínimo serio:

- `DEBUG = False`, `ALLOWED_HOSTS` desde el `.env` (lista explícita, nunca `["*"]`).
- CORS desde `CORS_ALLOWED_ORIGINS` del `.env` (sin `CORS_ALLOW_ALL_ORIGINS`).
- Postgres vía `DATABASE_URL` (las variables `POSTGRES_*` ya están en el `.env`).
- Solo `JSONRenderer` en `REST_FRAMEWORK` (sin API navegable).
- Cabeceras de seguridad estándar: `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`,
  `CSRF_COOKIE_SECURE`, `SECURE_HSTS_SECONDS`, `SECURE_PROXY_SSL_HEADER`, todas leídas del `.env`
  con defaults seguros.
- Logging a consola con nivel WARNING (para que lo capture el proceso del servidor).

Actualizá `backend/.env.example` con las variables nuevas que use `prod.py`.

---

## 4. Limpieza del `.env`

En `backend/.env`:

- Agregá `http://localhost:8001` y `http://127.0.0.1:8001` a `CORS_ALLOWED_ORIGINS` (hoy solo
  tiene `5173` y `3000`, que son de una SPA que este proyecto no es; el frontend se sirve en 8001
  según `FRONTEND_URL`).
- Comentá `GOOGLE_REDIRECT_URI`: apunta a `/api/auth/google/callback/`, una ruta que no existe.
  El login con Google usa `GoogleAuthView` en `POST /api/v1/auth/google/`, que recibe el ID token
  desde el frontend. Ninguna parte del código lee esa variable.
- Replicá ambos cambios en `.env.example`.

**No toques** el resto del `.env`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, las credenciales
de SMTP y `FRONTEND_URL` están bien y en uso.
