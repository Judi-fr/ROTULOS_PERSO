# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

ROTULOS_PERSO is a label/sign generation app for **Buspack**, a nationwide (Argentina) light-parcel
delivery company (500+ points of sale, 600+ destinations, partnered long-distance bus companies as
carriers). A "rótulo" here is a shipping/waybill LABEL that gets stuck on a parcel travelling on a bus —
not a product tag — with sender, recipient, address, postal code, city/province, order number and a QR
that gets scanned at the point of sale and at the terminal. The app is split into a Django REST API
(`backend/`) and a static multi-page frontend (`frontend/`, plain HTML/CSS/JS, no build step). `accounts`
(authentication, user administration, roles/permissions, support inbox), `orders` (addresses/orders,
self-service + admin "all orders" view), `audit` (read-only audit trail) and `labels` (label templates +
concrete labels, self-service + admin "all labels" view) are implemented; `documents` and `processing`
are still scaffolded Django apps with empty models/views/urls, reserved for uploading files and
converting them to a label format. There is no point-of-sale/destination catalog yet — `Address` stores
city/state as free text and `Order.carrier` awaits the partner's tracking API — don't assume those exist.

## Commands

All backend commands run from `backend/` with the venv active:

```bash
cd backend
source venv/bin/activate
```

- Run the dev server: `python manage.py runserver` (defaults to `config.settings.dev` via `manage.py`)
- Run all tests: `python manage.py test`
- Run one app's tests: `python manage.py test apps.accounts`
- Run a single test case / method: `python manage.py test apps.accounts.tests.LoginTests.test_login_normaliza_email`
- Make/apply migrations: `python manage.py makemigrations` / `python manage.py migrate`
- Django shell: `python manage.py shell`

Settings module is selected via `DJANGO_SETTINGS_MODULE` (`config.settings.dev` or `config.settings.prod`);
`manage.py` defaults to `dev`. Both import everything from `config/settings/base.py`.

### Docker (Postgres + API)

```bash
cd backend
docker-compose up
```

Runs Postgres 17 plus the API with `runserver` and autoreload (code mounted as a volume). Requires a
`.env` in `backend/` — copy `.env.example` and fill in `SECRET_KEY`, `POSTGRES_PASSWORD`, etc. Without
`DATABASE_URL` set, Django falls back to local SQLite (`backend/db.sqlite3`), so tests/dev work without
Docker or Postgres.

### Frontend

No build step. Open the HTML files directly or serve `frontend/` with any static server. Frontend JS
files hardcode the API base URL as `http://127.0.0.1:8000/api/v1/...` (see `assets/js/*.js`) — there is
no env-based config, so keep the backend on that host/port for the existing frontend to work as-is.

## Architecture

### Backend app layout

- `config/settings/base.py` — all shared settings (env vars via `django-environ`, reads `backend/.env`).
  `dev.py` and `prod.py` layer on top (dev: `ALLOWED_HOSTS = ["*"]`, CORS wide open, browsable API,
  console email backend by default; prod: HTTPS/HSTS/secure-cookie settings).
- `config/urls.py` — all routes are versioned under `/api/v1/`. `apps.accounts.urls` mounts under
  `/api/v1/auth/`, `apps.accounts.management_urls` (the admin user CRUD) mounts at `/api/v1/` (so its
  router path resolves to `/api/v1/users/`), `apps.accounts.admin_urls` (admin support inbox) also
  mounts at `/api/v1/` (`/api/v1/support-messages/`), `apps.orders.urls` mounts at `/api/v1/`
  (`/api/v1/addresses/`, `/api/v1/orders/`, `/api/v1/admin/orders/`), `apps.audit.urls` mounts at
  `/api/v1/audit/` (`/logs/`, `/actions/`), `apps.labels.urls` mounts at `/api/v1/labels/`
  (`/labels/`, `/templates/`, `/admin/`), and `documents`/`processing` mount at their own
  `/api/v1/<app>/` prefixes but currently expose empty `urlpatterns`.
- REST Framework is closed by default: `DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]` project-wide, so
  any new endpoint needs `permission_classes = [AllowAny]` explicitly to be public. JWT auth only
  (`djangorestframework-simplejwt`), access token in `Authorization: Bearer <token>`.

### Auth & identity model

- **Email is the username.** Every login path (Google, email/password, register) normalizes the email
  (`views.normalize_email`) and uses it as `User.username`, so `Juan@Gmail.com` and `juan@gmail.com`
  resolve to the same account regardless of how they signed up.
- All login endpoints (`GoogleAuthView`, `LoginView`, `RegisterView`) return the same shape via
  `views.auth_response` / `views.user_payload`: `{access, refresh, user: {..., role, permissions}}`, so
  the frontend never branches on login method.
- Refresh tokens rotate and get blacklisted on rotation/logout (`ROTATE_REFRESH_TOKENS` +
  `BLACKLIST_AFTER_ROTATION` in `SIMPLE_JWT`, `rest_framework_simplejwt.token_blacklist` app). Password
  reset also revokes *all* outstanding refresh tokens for the user (`blacklist_all_refresh_tokens`) in
  case the account was compromised.
- Password rules are enforced in two layers everywhere a password is set (register, admin-create,
  change-password, reset-confirm): `views.validate_password_strength` (project rule: 6+ chars, one
  uppercase, one digit) plus Django's own `AUTH_PASSWORD_VALIDATORS`.

### Roles & permissions (`apps/accounts`)

There is **no `role` field on `User`** — the role is derived, never stored directly:

- The source of truth for "which role does this user have" is Django `Group` membership, resolved by
  `permissions_map.get_effective_role()`: checks canonical groups (`admin`, `designer`, `operator`,
  `subscriber`) first, falls back to a custom Group name (custom roles), then to `is_staff → admin`,
  then to `subscriber`. `serializers.get_user_role` is just an alias of this function — do not
  reimplement role resolution elsewhere.
- The source of truth for "which atomic permissions does this role have" is the `GroupRolePermission`
  DB table (Group ↔ `RolePermission`), managed under `/api/v1/auth/roles/` and
  `/api/v1/auth/permissions/` (`role_permission_views.py`). `permissions_map.ROLE_PERMISSIONS` is only a
  static fallback used if that table isn't queryable yet (e.g. mid-migration).
- `permissions_map.user_has_permission(user, key)` is the single check to call from views;
  `role_permissions.HasRolePermission` wraps it as a DRF permission class. `UserAdminViewSet` instead
  calls `user_has_permission` directly per-action (`_require_permission`,
  `_required_update_permissions`) because different HTTP methods/fields require different permissions
  (e.g. editing `status` needs `users.reactivate`/`users.deactivate`, not `users.edit`).
- Legacy/alias role names (`"user"`, `"administrador"`, `"diseñador"`, ...) are normalized by
  `permissions_map.normalize_role()`. Unrecognized roles are treated as valid custom roles and preserved
  (not collapsed to the default) so custom-role permission assignment keeps working.
- When adding a new permission-gated action: add the permission key to `permissions_map.PERMISSIONS`,
  seed it via `RolePermission`, and gate the view/serializer field with `user_has_permission`. Don't add
  a new source of role truth (no new `role` field, no separate group lookup).
- Admin-only permissions (assigned **only** to the `admin` group, not to the self-service roles):
  `audit.view`, `orders.view_all`, `support.view_all`, `support.manage` — seeded by
  `accounts/migrations/0014_seed_admin_only_permissions.py` — plus `labels.view_all` and
  `labels.manage_templates`, seeded (together with the self-service `labels.view`/`create`/`edit`/
  `delete`) by `accounts/migrations/0015_seed_label_permissions.py`.

### Audit trail (`apps/audit`) and admin support inbox

- `apps.audit.AuditLog` is the single audit model: `actor` (FK, `SET_NULL`) + `actor_email` snapshot,
  `category`/`action` (`TextChoices`), plain `target_type`/`target_id`/`target_repr` (no
  `GenericForeignKey`/contenttypes — simpler to query/test), a `changes` JSON diff
  (`{"field": {"from": ..., "to": ...}}`, **never** passwords/hashes/tokens), IP/user-agent, and
  `created_at`. It's immutable: `save()` refuses to update an existing row and `delete()` always
  raises — there's no write/delete endpoint, only `GET /api/v1/audit/logs/` (filters: `search`,
  `category`, `action`, `actor`, `target_type`, `date_from`/`date_to`, `ordering`; permission
  `audit.view`) and `GET /api/v1/audit/actions/` (catalog for the frontend selects).
- The only way to create a row is `apps.audit.services.record(request=None, *, actor=None, category,
  action, target=None, ..., changes=None)`. It's called **explicitly** from the views that need it
  (no signals): `accounts/views.py` (login success/failure/lockout, logout, password
  change/reset), `accounts/viewsets.py` (`UserAdminViewSet` create/update/deactivate/reactivate/
  unlock/role change — diff never includes `password`), `accounts/role_permission_views.py` (role
  create/delete/permissions update), `accounts/support_views.py` (support create/status
  change/reply), and `apps/orders` (order create/cancel from the view with a known actor;
  `Order.save()` itself logs `order.status_change` with `actor=None` for edits with no request — e.g.
  Django admin — using `instance._skip_status_audit = True` to avoid duplicating what the view already
  logged). `record()` wraps the insert in `transaction.atomic()` and swallows/logs any exception —
  a failed audit write must never break the calling request.
- `SupportMessage` (in `apps.accounts.models`) grew `status` (`pending`/`in_progress`/`resolved`),
  `response`, `responded_at`, `handled_by`, `updated_at`. `SupportMessageView`
  (`/api/v1/auth/support/`) is now `ListCreateAPIView`: `GET` returns only the caller's own messages
  (permission `support.create`), `POST` unchanged. The admin inbox is
  `AdminSupportMessageViewSet` at `/api/v1/support-messages/` (list/retrieve need `support.view_all`,
  `PATCH` — `status`/`response` only — needs `support.manage`); saving a non-empty `response` sets
  `responded_at`/`handled_by` and auto-resolves a still-`pending` message unless `status` was sent
  explicitly.
- `GET /api/v1/admin/orders/` (`orders.view_all`, admin-only) lists every user's orders
  (`AdminOrderListView`/`AdminOrderSerializer` in `apps/orders`); the self-service `OrderViewSet`
  queryset is untouched.
- `DashboardView` adds `audit` (`gestionuser.html#audit`) and `support_inbox`
  (`gestionuser.html#support`) menu items only when the effective role is `admin`.

### Reports metrics endpoints

Four read-only, `?months=` (default 6, capped at 12) metrics endpoints back `reportsSection` in
`gestionuser.html`. Each domain computes its own metrics next to its models/permission, instead of one
endpoint doing everything:

- `GET /api/v1/users/metrics/` (`UserAdminViewSet.metrics`, permission `users.view`) — the original
  contract (`role_distribution`, `signups_by_month`, `auth_method`) is untouched, plus: Usuarios
  (`active_vs_inactive_by_month`, `active_users`, `retention`, `lockouts_by_month`,
  `top_failed_attempts`, `account_age`) and Seguridad (`email_verification`,
  `pending_password_change`, `auth_method_email_verification`). `active_vs_inactive_by_month`
  (deactivations) and `lockouts_by_month` read `apps.audit.AuditLog` (`user.deactivate` /
  `auth.lockout`) because `User`/`LoginLockout` only hold *current* state, not history.
- `GET /api/v1/orders/metrics/` (`apps.orders.views.OrderMetricsView`, permission `orders.view_all`) —
  `orders_by_month`, `orders_by_status` (funnel + `cancelled` separately), `cancellation_rate` (rate +
  prior-status breakdown, from the order's last `OrderStatusEvent` before cancelling),
  `avg_time_between_statuses` (hours, from consecutive `OrderStatusEvent` pairs),
  `top_users_by_orders`, `users_with_orders`, `orders_by_location` (`Address.city`/`state`).
- `GET /api/v1/support-messages/metrics/` (`AdminSupportMessageViewSet.metrics`, permission
  `support.view_all`) — `support_by_month`, `support_by_status`, `avg_response_time`.
- `GET /api/v1/audit/metrics/` (`apps.audit.views.AuditMetricsView`, permission `audit.view`) —
  `admin_activity` (`by_actor`/`by_action`), `login_events` (successful vs. failed logins by month).

Frontend: `admingestion_test.js` groups these into four blocks (Usuarios, Seguridad, Pedidos, Soporte y
actividad) with a shared period selector (3/6/12 months, `#reportsMonthsSelect` + `#reportsRefreshBtn`).
Pedidos/Soporte y actividad are fetched only when the user has the corresponding permission
(`canUseUserPermission`) and hide themselves (not an error) otherwise; every renderer treats
empty/`null` as "Sin datos todavía", never a fabricated number.

### Labels / rótulos (`apps/labels`)

A "rótulo" is the waybill label stuck on a Buspack parcel — sender, recipient (`Label.client`),
address, postal code, city/province, order number and a QR — not a product label. The frontend editor
(`frontend/pedidos/diseñorotulos.html`) already defines the design format and the backend just
validates and persists it, it doesn't reinvent it: `design` is a dict keyed by the editor's field names
(`logo`, `qr`, `remitente`, `destinatario`, `domicilio`, `cp`, `localidad`, `pedido`), each value
`{"left": <0-100>, "top": <0-100>, "text": <optional str>}` — position in **percent** of the label so it
survives a size change. There's no point-of-sale/destination catalog yet (see Project overview) — don't
add `PickupPoint`/`Destination` models here.

- `LabelTemplate` — reusable base design: `owner` (`SET_NULL`, `None` = system template),
  `is_public`, `width_cm`/`height_cm` (5-30 / 5-40, validated in the serializer, not the model),
  `design`, `preview` image.
- `Label` — a concrete, printable label: `user` (`CASCADE`), `template` (`SET_NULL`, optional),
  `order` (`SET_NULL`, optional FK to `apps.orders.Order` — the central case: a label normally belongs
  to a real shipment), `client` (the recipient), `width_cm`/`height_cm`, `design`, `logo`/`thumbnail`
  images, `is_active` (**soft-delete**, same as users — a label is never hard-deleted).
- Serializers validate `design` for real (`serializers.validate_design`: unknown keys or out-of-range
  `left`/`top` return 400) and accept `logo`/`thumbnail` either as a real file (`multipart/form-data`)
  or as the base64 data URL the editor already produces (`ImageOrDataUrlField` decodes+verifies with
  Pillow, rejects non-images, caps size at 2 MB for `logo` / 300 KB for `thumbnail`/`preview`). If
  `order` is set it must belong to the requesting user (admin bypasses this, per
  `permissions_map.get_effective_role`); `template` must be public or owned. Image URLs come back
  absolute because the serializer context carries `request` (DRF's `ImageField.to_representation`
  builds the absolute URI automatically).
- Permissions: self-service `labels.view`/`create`/`edit`/`delete` (all four canonical roles) plus
  admin-only `labels.view_all`/`labels.manage_templates` — see Roles & permissions above.
  `LabelViewSet.get_permissions()` maps action → permission the same way `OrderViewSet` does.
- `GET/POST /api/v1/labels/labels/`, `GET/PATCH/DELETE /api/v1/labels/labels/<id>/` — CRUD of the
  caller's own labels (`LabelViewSet`, queryset always `user=request.user, is_active=True`); `DELETE`
  is a soft-delete (`is_active=False`), never a real row delete. `POST
  /api/v1/labels/labels/<id>/duplicate/` clones a label (`name + " (copia)"`) as an independent row —
  "start from a previous one".
- `GET/POST /api/v1/labels/templates/` — `LabelTemplateViewSet`: list/retrieve is open to any
  authenticated user (public templates + the caller's own), create/update/delete requires
  `labels.manage_templates`.
- `GET /api/v1/labels/admin/` (`labels.view_all`, admin-only, paginated) — every user's labels
  (`AdminLabelListView`/`AdminLabelSerializer`), filters `search`/`user`/`template`/`is_active`/
  `date_from`/`date_to`; the self-service `LabelViewSet` queryset is untouched, same pattern as
  `AdminOrderListView`.
- Audit: `label.create`/`label.update`/`label.delete` and `template.create`/`update`/`delete` are
  logged via `apps.audit.services.record` (added to `AuditLog.Category`/`Action` as a `labels`
  category).
- `Pillow` (in `backend/requirements.txt`) is required for `ImageField` and for verifying uploaded/
  decoded images.

### Frontend

Plain multi-page app, one HTML file per screen, no framework/bundler:

- `index.html` — login/register (email+password and Google Sign-In). `assets/js/google.js` /
  `google_test.js` and `assets/apis/access.php` are leftover PHP-era prototypes for Google login that
  are **not** part of the current flow — the real Google auth goes through `GoogleAuthView` in Django.
- `dashboard.html` + `assets/js/dashboard.js` — landing screen after login (admins can also land here
  via the "Panel" sidebar link in `gestionuser.html`, or return to it after logging in from
  `index.html`). The menu items and whether the "users" section is enabled come entirely from the
  backend (`DashboardView` / `/api/v1/auth/users/me/dashboard/`), based on the user's effective role —
  the frontend just renders whatever it's given, it doesn't decide visibility by role itself. When
  `user.is_admin` is true, a "Volver al panel de administración" link (→ `gestionuser.html`) appears
  in the topbar.
- `gestionuser.html` + `assets/js/admingestion_test.js` — admin user CRUD + roles/permissions panel,
  plus (same show/hide-section pattern as Roles/Reportes, gated by `canUseUserPermission()`) an
  auditoría section (`#auditSection`, `navAudit`: paginated `AuditLog` table with filters + a "changes"
  detail modal, and — inside the same section — the admin "all orders" table) and a support inbox
  section (`#supportSection`, `navSupport`: status counters, paginated list + a detail panel to change
  `status`/write `response`). Talks to `/api/v1/users/`, `/api/v1/auth/me/`, `/api/v1/auth/roles/`,
  `/api/v1/auth/permissions/`, `/api/v1/audit/logs/`, `/api/v1/audit/actions/`,
  `/api/v1/admin/orders/`, `/api/v1/support-messages/`. `isAdminMode()` / `canUseUserPermission()`
  there are UI-only gating — the backend is always the real authority and re-checks every permission
  server-side.
- `ayuda.html` + `assets/js/ayuda.js` — support contact form; below it, "Mis mensajes" lists the
  user's own messages (`GET /api/v1/auth/support/`) with their status and the admin's response, if any.
- Every page's fetch wrapper (`apiFetch`) attaches `Authorization: Bearer <access>` from
  `localStorage`, and on a 401 clears `access`/`refresh`/`user` from `localStorage` and redirects to
  `index.html`.
- `plantillas_rotulos.html` + `assets/js/dashboard_rotulos.js` + `assets/css/gestionrotulos.css` —
  grid of the user's own labels (thumbnail, recipient, date), search, a preview modal, and
  Editar/Duplicar/Eliminar actions. `frontend/pedidos/diseñorotulos.html` — the label editor (drag
  fields, logo, QR, size in cm, PNG/PDF export); its `saveBtn` builds the payload in the EDITOR's own
  shape (`{nombre, cliente, thumbnail, size:{widthCm,heightCm}, logo, fields, order}`).
  `frontend/pedidos/api.js` is the thin adapter both pages import: it translates that shape to/from
  the `apps.labels` API shape (`name`/`client`/`width_cm`/`height_cm`/`design`/...), and carries the
  same `apiFetch`/401/403 handling as the rest of the frontend. Because `api.js` lives at
  `frontend/pedidos/api.js` but is imported from pages at different depths (the editor itself, and
  `dashboard_rotulos.js` for the root-level `plantillas_rotulos.html`), its session-redirect URLs are
  built from `import.meta.url` rather than a hardcoded relative path — don't replace that with a plain
  `"../index.html"` string, it would break for whichever page is at the other depth. `DashboardView`'s
  `labels` menu item now points at `plantillas_rotulos.html` (`enabled: true`); `documents`/
  `processing` are still `enabled: false` with no URL.

### Multiple sqlite files

`backend/db.sqlite3*` — there are several backup copies checked into the working tree
(`db.sqlite3.backup-antes-de-reparar`, `db.sqlite3-backup-crud`, `db.sqlite3-con-admin-recuperado`).
Only `db.sqlite3` is the live dev database; the others are manual recovery snapshots — don't delete them
without checking with the user, and don't treat them as fixtures.

## Reglas

- Nunca ejecutar `git pull`, `git push` ni `git commit`. Solo copia local.
- Nunca modificar tests para que pasen: corregir la implementación.
- Nunca usar URLs placeholder. La API real es http://127.0.0.1:8000/api/v1/
- No borrar archivos de Google/OAuth (google.js, google_test.js, access.php,
  oauth-test/index.html, index_test.html) aunque parezcan código muerto.
- Antes de modificar: inspeccionar el código, identificar la causa exacta, cambio mínimo.
  Nada de refactors grandes para problemas chicos.
- Si el problema es de backend no tocar el frontend, y viceversa.

## Arquitectura

- Validación, permisos, reglas de negocio y autorización van en el backend.
  El frontend solo presenta.
- Identidad siempre desde request.user (JWT), nunca desde un ID que manda el cliente.
- Roles: admin, designer, operator, subscriber. Legacy "user" = "subscriber".
  Admin = grupo admin + is_staff.
