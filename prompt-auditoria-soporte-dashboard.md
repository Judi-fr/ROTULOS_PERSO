# Prompt para Claude Code — Auditoría, bandeja de soporte y acceso del admin al dashboard

Trabajás sobre ROTULOS_PERSO (Django REST + frontend HTML/JS estático). Respetá `CLAUDE.md`:
inspeccionar antes de modificar, cambio mínimo, validación/permisos/reglas de negocio en el
backend (el frontend solo presenta), identidad siempre desde `request.user` (JWT), nunca URLs
placeholder (la API real es `http://127.0.0.1:8000/api/v1/`), no modificar tests existentes para
que pasen, no borrar archivos de Google/OAuth, y **no** hacer `git pull/push/commit`.

Implementá **tres cosas relacionadas**, en este orden, corriendo `python manage.py test` al
terminar cada parte:

---

## Parte 1 — Registro de auditoría (nuevo)

Hoy no existe ningún modelo de auditoría. Creá una app Django nueva `apps.audit` (misma
estructura que `apps.orders`), registrada en `INSTALLED_APPS` y montada en `config/urls.py` bajo
`/api/v1/audit/`.

### Modelo `AuditLog`

- `actor` → FK a `settings.AUTH_USER_MODEL`, `null=True`, `on_delete=SET_NULL`, `related_name="audit_entries"`.
- `actor_email` → `CharField(max_length=254, blank=True, default="")`: snapshot del email al momento
  del evento, para que el registro siga siendo legible aunque la cuenta cambie de email.
  En eventos sin actor autenticado (login fallido, por ejemplo) guardá el email intentado.
- `category` → `TextChoices`: `auth`, `users`, `roles`, `orders`, `support`.
- `action` → `TextChoices` con valores explícitos:
  - auth: `auth.login_success`, `auth.login_failed`, `auth.lockout`, `auth.logout`,
    `auth.password_change`, `auth.password_reset`
  - users: `user.create`, `user.update`, `user.deactivate`, `user.reactivate`, `user.unlock`,
    `user.role_change`
  - roles: `role.create`, `role.update`, `role.delete`, `role.permissions_update`
  - orders: `order.create`, `order.status_change`, `order.cancel`
  - support: `support.create`, `support.status_change`, `support.reply`
- `target_type` (`CharField`, ej. `"user"`, `"order"`, `"role"`, `"support_message"`),
  `target_id` (`CharField`, no `IntegerField`: los ids de Group también entran acá),
  `target_repr` (`CharField`, texto legible: email del usuario afectado, `"Pedido #12"`, etc.).
  Nada de `GenericForeignKey`/`contenttypes`: campos planos, más simples y suficientes para leer.
- `changes` → `JSONField(default=dict, blank=True)` con el diff `{"campo": {"from": ..., "to": ...}}`.
  **Nunca** guardes contraseñas, hashes ni tokens ahí: si el campo es sensible, registrá solo el
  nombre del campo con valores `"***"`.
- `ip_address` (`GenericIPAddressField(null=True, blank=True)`), `user_agent` (`CharField(max_length=300, blank=True, default="")`).
- `created_at` (`auto_now_add`), `Meta.ordering = ["-created_at"]`, índices en `created_at`,
  `category` y `actor`.
- El registro es **inmutable**: sobreescribí `save()` para que lance si la instancia ya tiene `pk`,
  y no expongas ningún endpoint de escritura/borrado.

### Servicio de registro

`apps/audit/services.py` con `record(request=None, *, actor=None, category, action, target=None, target_type="", target_id="", target_repr="", changes=None, actor_email="")`
que crea el `AuditLog`, extrae IP (`REMOTE_ADDR`, respetando `HTTP_X_FORWARDED_FOR` si viene) y
`HTTP_USER_AGENT` del request, y **nunca rompe el flujo principal**: envolvé la creación en
try/except con `logger.exception`, porque que falle una auditoría no puede tumbar un login.

Llamalo **explícitamente** desde las vistas (nada de señales globales: menos magia, más fácil de
testear y de seguir):

- `apps/accounts/views.py`: `LoginView` (éxito y fallo), `GoogleAuthView`, `LogoutView`,
  `ChangePasswordView`, `PasswordResetConfirmView`, y donde hoy se dispara el lockout
  (`LoginLockout`) → `auth.lockout`.
- `apps/accounts/viewsets.py` (`UserAdminViewSet`): create / update / desactivar / reactivar /
  unlock / cambio de rol. En `update` armá el diff comparando los valores previos con los nuevos
  y registrá `user.role_change` aparte cuando cambie el rol.
- `apps/accounts/role_permission_views.py`: alta/edición/borrado de roles y guardado de permisos
  (en `role.permissions_update`, el diff son los permisos agregados y quitados).
- `apps/orders/views.py`: creación y cancelación de pedidos. Para los cambios de estado hechos
  desde el admin de Django o a mano, registrá desde el mismo punto donde `Order.save()` crea el
  `OrderStatusEvent` — sin actor cuando no hay request.
- Soporte: ver Parte 2.

### Endpoints

- `GET /api/v1/audit/logs/` — solo lectura, paginado con la `pagination.py` existente (page_size 20,
  mismo shape `count/from/to/total_pages/...` que ya consume el frontend). Filtros combinables con
  AND vía query params: `search` (sobre `actor_email`, `target_repr`, `action`), `category`,
  `action`, `actor` (id o email), `target_type`, `date_from`, `date_to`, `ordering`
  (`-created_at` por defecto).
- `GET /api/v1/audit/actions/` — catálogo `{categories: [...], actions: [{key, label, category}]}`
  para poblar los selects del frontend sin hardcodearlos ahí.
- Permiso nuevo `audit.view`, chequeado con `HasRolePermission` (no con `IsAdminUser` suelto):
  seguí el patrón de `permissions_map.PERMISSIONS` + migración de seed. Asignado **solo a admin**.

### Pedidos de todos los usuarios

Además del log, el admin tiene que poder ver **todos los pedidos**, no solo el rastro de eventos:

- Permiso nuevo `orders.view_all` (solo admin).
- `GET /api/v1/admin/orders/` (o `orders/all/`, elegí lo que encaje mejor con el router actual de
  `apps.orders`): lista paginada de todos los pedidos con email del usuario, dirección, estado,
  fechas y último evento del timeline; filtros `search` (email/descripción), `status`, `user`,
  `date_from`/`date_to`. Solo lectura — el admin no crea ni cancela pedidos ajenos desde acá.
- El queryset de `OrderViewSet` **no cambia**: sigue recortado a `request.user`. Este es un
  endpoint aparte, con su propio permiso.

---

## Parte 2 — Bandeja de soporte en el panel admin

Hoy `SupportMessage` solo se crea (`POST /api/v1/auth/support/`) y se lee desde `/admin/`.

### Modelo

Ampliá `SupportMessage` (migración `0013_...`): `status` (`TextChoices`: `pending` "Pendiente",
`in_progress` "En curso", `resolved` "Resuelto"; default `pending`), `response` (`TextField`,
blank), `responded_at` (`DateTimeField`, null), `handled_by` (FK a User, `null=True`,
`on_delete=SET_NULL`, `related_name="handled_support_messages"`), `updated_at` (`auto_now`).

### Endpoints admin

Nuevos permisos `support.view_all` y `support.manage`, **solo admin**, sembrados por migración
(mismo patrón idempotente que `0012_seed_support_permission.py`) y agregados a
`permissions_map.PERMISSIONS`:

- `GET /api/v1/support-messages/` — lista paginada (misma paginación), con filtros `search`
  (email del usuario, asunto, mensaje), `status`, `date_from`/`date_to` y `ordering`.
  Devolvé también un bloque de conteos por estado para las tarjetas del panel.
- `GET /api/v1/support-messages/<id>/` — detalle.
- `PATCH /api/v1/support-messages/<id>/` — solo `status` y `response`. Al guardar una respuesta no
  vacía: setear `responded_at`, `handled_by = request.user` y, si el status seguía en `pending`,
  pasarlo a `resolved`. Registrar en auditoría `support.status_change` y/o `support.reply`.
- `GET` y `PATCH` exigen `support.view_all` / `support.manage` respectivamente.

### Endpoint del usuario

`SupportMessageView` pasa de `CreateAPIView` a `ListCreateAPIView`: el `GET` devuelve **solo los
mensajes propios** (`user=request.user`, nunca un id por parámetro) con `status`, `response` y
`responded_at`, para que el usuario vea la respuesta del admin. El `POST` no cambia de contrato.
El permiso de lectura propia es `support.create` (ya lo tienen los cuatro roles) o uno nuevo
`support.me.view` si te resulta más limpio — si lo agregás, sembralo para los cuatro roles.

---

## Parte 3 — El admin entra al dashboard normal

`frontend/index.html` sigue mandando al admin a `gestionuser.html` tras el login (no cambies eso).
Lo que falta es que pueda ir y volver:

- En `gestionuser.html`, la entrada **"Panel"** del sidebar hoy es `href="#"` muerto: cableala a
  `dashboard.html`.
- En `dashboard.html` / `dashboard.js`, cuando el backend responde `user.is_admin === true`,
  mostrá un enlace "Volver al panel de administración" → `gestionuser.html`. El dato ya viene en
  `/api/v1/auth/users/me/dashboard/`, no agregues lógica de rol en el frontend.
- Verificá que el rol `admin` tenga efectivamente los permisos self-service (`orders.*`,
  `addresses.manage`, `support.create`, `users.me.*`) para que el dashboard le funcione igual que a
  cualquier usuario. Si falta alguno, sembralo por migración — no lo resuelvas con un `if` en la vista.
- El `DashboardView` sigue armando el menú en el backend. Agregá al menú, **solo para admin**, los
  ítems `audit` ("Registros de auditoría", `gestionuser.html#audit`) y `support_inbox`
  ("Mensajes de soporte", `gestionuser.html#support`), con el mismo shape `{key,label,url,enabled}`.

---

## Frontend del panel admin

En `gestionuser.html` + `frontend/assets/js/admingestion_test.js`, siguiendo **exactamente** el
patrón que ya usan `rolesSection` y `reportsSection` (secciones ocultas + `navX` que alterna
visibilidad, listeners delegados, `apiFetch` con `Authorization` y manejo de 401):

1. **Registros de auditoría**: la entrada del sidebar ya existe con `href="#"` muerto — dale
   `id="navAudit"` y una `<section id="auditSection">` con tabla paginada (fecha, actor, acción,
   objetivo, detalle de cambios) y filtros poblados desde `/api/v1/audit/actions/`. El detalle de
   `changes` puede mostrarse en un modal reusando el patrón de `viewUserModal`.
2. **Soporte**: agregá un ítem nuevo al sidebar (`id="navSupport"`) y una
   `<section id="supportSection">` con tarjetas de conteo por estado, tabla paginada con filtros, y
   un panel de detalle donde el admin lee el mensaje, cambia el estado y escribe la respuesta.
3. **Pedidos de todos los usuarios**: dentro de la sección de auditoría (o como sección hermana,
   lo que quede más limpio), una tabla paginada contra el endpoint admin de pedidos.
4. Los ítems admin se muestran según los permisos que ya vienen en `user.permissions` de
   localStorage (`canUseUserPermission()` existente) — recordá que eso es **solo gating de UI**:
   el backend revalida todo.

En `ayuda.html` + `ayuda.js`: debajo del formulario, listá los mensajes propios con su estado y,
cuando exista, la respuesta del admin.

Estilos en `frontend/assets/css/gestionuser.css` reutilizando las clases existentes
(`stat-value`, `filter-panel`, `pager`, etc.). Sin frameworks ni build step nuevos.

---

## Tests

Agregá tests nuevos (sin tocar los existentes) que cubran al menos:

- Un no-admin recibe 403 en `/api/v1/audit/logs/`, en `/api/v1/support-messages/` y en el listado
  admin de pedidos; el admin recibe 200.
- Un login exitoso, uno fallido y un lockout dejan su `AuditLog` con la acción correcta.
- Crear, editar, desactivar y cambiar el rol de un usuario desde el CRUD admin genera las entradas
  esperadas, y el diff de `changes` no contiene la contraseña.
- Los filtros de auditoría (`category`, `action`, `date_from`/`date_to`, `search`) combinan con AND.
- Un `AuditLog` existente no se puede modificar ni borrar por la API.
- Un usuario ve **solo sus propios** mensajes de soporte en el `GET`; el admin los ve todos.
- El `PATCH` de un mensaje con respuesta setea `responded_at` y `handled_by`, y deja el evento
  de auditoría correspondiente.
- El menú del dashboard de un admin incluye `audit` y `support_inbox`; el de un subscriber, no.

Al final: `python manage.py test` en verde (hoy hay 121/121 pasando; no debe romperse ninguno) y
actualizá `CLAUDE.md` con la app `apps.audit`, los permisos nuevos y las rutas agregadas.
