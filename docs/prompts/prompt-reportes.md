# Prompt para Claude Code — Ampliar la sección Reportes del panel admin

Trabajás sobre ROTULOS_PERSO. Respetá `CLAUDE.md`: cambio mínimo, la lógica va en el backend
(el frontend solo pinta lo que recibe), URLs reales (`http://127.0.0.1:8000/api/v1/`), nada de
`git pull/push/commit`. **No escribas tests ni corras la suite en esta tarea**: solo implementá.

Hoy `UserAdminViewSet.metrics` (`GET /api/v1/users/metrics/`) devuelve tres cosas
(`role_distribution`, `signups_by_month`, `auth_method`) y `gestionuser.html` las pinta en
`reportsSection` con barras simples. Ampliá eso.

## Backend

Todo lo nuevo se calcula **en el backend**, sobre datos que ya existen — no agregues modelos.
Mantené el permiso `users.view` y el parámetro `months` (default 6, tope 12) que ya usa `metrics`.

Si un bloque crece mucho, dividí en endpoints por dominio (`users/metrics/`, más
`orders/metrics/` en `apps.orders` con permiso `orders.view_all`, y `support`/`audit` en su app si
esas partes ya están implementadas). Elegí lo que quede más limpio, pero **no rompas el contrato
actual de `/api/v1/users/metrics/`**: las tres claves existentes deben seguir devolviéndose igual,
porque el frontend ya las consume.

### Usuarios (`User`, `LoginLockout`)

- `active_vs_inactive_by_month`: altas y bajas (desactivaciones) por mes, con el neto.
- `active_users`: usuarios con `last_login` en los últimos 7 / 30 / 90 días, más `never_logged_in`.
- `retention`: por cada mes de registro, cuántos de esos usuarios volvieron a loguearse alguna vez.
- `lockouts_by_month` y `top_failed_attempts`: cuentas con más intentos fallidos (top 10, email +
  cantidad + si está bloqueada ahora).
- `account_age`: antigüedad promedio de las cuentas, en días, y desglose por rol.

### Seguridad de cuentas (`EmailVerification`, `PasswordChangeRequirement`)

- `email_verification`: verificados / sin verificar / con token vencido.
- `pending_password_change`: cuántas cuentas siguen con cambio de contraseña forzado pendiente.
- Cruce `auth_method` × verificación de email.

### Pedidos (`Order`, `OrderStatusEvent`, `Address`) — el bloque más importante

- `orders_by_month` y `orders_by_status` (embudo completo: created → preparing → dispatched →
  in_transit → delivered, más cancelled aparte).
- `cancellation_rate`: % de pedidos cancelados y en qué estado estaban al cancelarse.
- `avg_time_between_statuses`: promedio de horas entre estados consecutivos, calculado desde
  `OrderStatusEvent` (ya guarda el timeline, no hace falta nada nuevo).
- `top_users_by_orders`: top 10 por cantidad de pedidos, con email y fecha del último.
- `users_with_orders`: % de usuarios que hicieron al menos un pedido.
- `orders_by_location`: conteo por ciudad y por provincia, desde `Address`.

### Soporte y auditoría — **solo si esas partes ya están implementadas en el repo**

Verificá primero si existen `apps.audit` y los campos `status`/`response` en `SupportMessage`.
Si no están, salteá este bloque entero sin crear nada.

- `support_by_month`, `support_by_status`, `avg_response_time`.
- `admin_activity`: acciones por administrador y por tipo de acción.
- `login_events`: logins exitosos vs fallidos por mes.

## Frontend

En `reportsSection` de `gestionuser.html` + su código en `admingestion_test.js`:

- Agrupá las métricas en bloques con subtítulo: **Usuarios**, **Seguridad**, **Pedidos**, y
  **Soporte y actividad** (este último solo si el backend lo devuelve — si la clave no viene, no
  muestres la sección vacía).
- Reutilizá las clases y el patrón visual que ya existen (`metrics-bars`, `metrics-stat-row`,
  `stat-value`): barras horizontales para distribuciones, fila de números grandes para totales,
  tabla simple para los "top 10". **Sin librerías de gráficos ni build step nuevo.**
- Un selector de período (3 / 6 / 12 meses) arriba de la sección que re-pida las métricas con
  `?months=`, y un botón "Actualizar".
- Los números que sean porcentajes o promedios formatealos en el frontend (1 decimal), pero el
  cálculo viene hecho del backend.
- Manejo de vacío: si una métrica no tiene datos, mostrá "Sin datos todavía" — nunca un número
  inventado ni un `—` sin explicación (mismo criterio que ya se usó con "Locked Users").

Al terminar, actualizá `CLAUDE.md` con los endpoints de métricas nuevos y las claves que devuelven.
