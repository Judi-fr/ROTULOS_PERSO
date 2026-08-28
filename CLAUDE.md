# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

ROTULOS_PERSO is a label/sign generation app split into a Django REST API (`backend/`) and a static
multi-page frontend (`frontend/`, plain HTML/CSS/JS, no build step). Only the `accounts` app
(authentication, user administration, roles/permissions) is implemented; `documents`, `processing`,
and `labels` are scaffolded Django apps with empty models/views/urls, reserved for uploading
files, converting them to a label format, and generating/printing the actual rótulos.

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
  router path resolves to `/api/v1/users/`), and `documents`/`processing`/`labels` mount at their own
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

### Frontend

Plain multi-page app, one HTML file per screen, no framework/bundler:

- `index.html` — login/register (email+password and Google Sign-In). `assets/js/google.js` /
  `google_test.js` and `assets/apis/access.php` are leftover PHP-era prototypes for Google login that
  are **not** part of the current flow — the real Google auth goes through `GoogleAuthView` in Django.
- `dashboard.html` + `assets/js/dashboard.js` — landing screen after login. The menu items and whether
  the "users" section is enabled come entirely from the backend (`DashboardView` /
  `/api/v1/auth/users/me/dashboard/`), based on the user's effective role — the frontend just renders
  whatever it's given, it doesn't decide visibility by role itself.
- `gestionuser.html` + `assets/js/admingestion_test.js` — admin user CRUD + roles/permissions panel,
  talks to `/api/v1/users/`, `/api/v1/auth/me/`, `/api/v1/auth/roles/`, `/api/v1/auth/permissions/`.
  `isAdminMode()` / `canUseUserPermission()` there are UI-only gating — the backend is always the real
  authority and re-checks every permission server-side.
- Every page's fetch wrapper (`apiFetch`) attaches `Authorization: Bearer <access>` from
  `localStorage`, and on a 401 clears `access`/`refresh`/`user` from `localStorage` and redirects to
  `index.html`.
- `frontend/pedidos/` and `plantillas_rotulos.html` are early UI for the not-yet-built labels flow.

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
