# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

ROTULOS_PERSO is a **multi-client** shipping-label ("rótulo") app, distributed as an app installed in
online stores (Tiendanube first, Shopify maybe later): each client connects their store, their orders
arrive automatically, and the app generates the labels, dispatches and pushes tracking back. It is **not**
built for a single company — never hardcode a client, sender or carrier name as a default. A "rótulo" is a
shipping/waybill LABEL stuck on a parcel — not a product tag — with sender (the client/store that ships),
recipient (the buyer), address, postal code, city/province, order number, QR and barcode.

Backend: Django REST API (`backend/`), all routes under `/api/v1/` (`backend/config/urls.py`). Apps:
`accounts` (auth, user admin, roles/permissions, support inbox), `orders` (addresses/orders, manual
creation, CSV/Excel import, store sync, dispatch), `audit` (read-only trail), `labels` (two independent
label-rendering systems, see below), `integrations` (store connections, webhooks, event queue),
`documents` (generated batch output + uploaded source files) and `processing` (Claude vision agent that
reads a photographed label). Frontend: static multi-page app (`frontend/`), plain HTML/CSS/JS, no build
step.

Don't assume: there is no point-of-sale/destination catalog (`Address` stores city/state as free text),
there is no carrier API (`Order.carrier`/`tracking_number` are typed in when dispatching), and the app is
not scoped to one client or one carrier.

## Commands

All backend commands run from `backend/` with the venv active:

```bash
cd backend
source venv/bin/activate
```

- Dev server: `python manage.py runserver` (defaults to `config.settings.dev` via `manage.py`)
- All tests: `python manage.py test`
- One app: `python manage.py test apps.accounts`
- One test: `python manage.py test apps.accounts.tests.test_accounts.LoginTests.test_login_normaliza_email`
- Migrations: `python manage.py makemigrations` / `python manage.py migrate`
- Django shell: `python manage.py shell`
- Store integrations worker (processes `IntegrationEvent`, retries): `python manage.py run_integrations_worker`
  (`--once` for a single batch, `--limit`, `--sleep`)

`DJANGO_SETTINGS_MODULE` selects `config.settings.dev` or `config.settings.prod`; both import from
`config/settings/base.py`. `manage.py` defaults to `dev`.

### Docker (Postgres + API + worker)

`cd backend && docker-compose up` — Postgres 17, the API with `runserver`/autoreload, and the
`run_integrations_worker` process (`restart: unless-stopped`, no ports). Without that worker running,
store integrations do nothing on their own: webhooks only enqueue `IntegrationEvent` rows. Stuck or
failed events are visible in the Django admin (`IntegrationEvent`, filters by status/event type). Needs `backend/.env`
(copy `.env.example`). Without `DATABASE_URL`, Django falls back to local SQLite (`backend/db.sqlite3`) —
tests/dev work without Docker or Postgres. `backend/db.sqlite3*` also has manual recovery snapshots
checked in (`.backup-antes-de-reparar`, `-backup-crud`, `-con-admin-recuperado`) — only plain `db.sqlite3`
is the live one; don't delete the others or treat them as fixtures.

### Frontend

No build step. Open the HTML files directly or serve `frontend/` with any static server. The API base URL
lives only in `assets/js/config.js` (`window.APP_CONFIG.API_BASE`, `http://127.0.0.1:8000/api/v1`), loaded
first on every page; every other script builds URLs on it.

## Architecture

### Backend URL map (`config/urls.py`)

- `api/v1/auth/` → `apps.accounts.urls` (login/register/Google/logout/password-reset/verify-email, `me/`,
  `me/change-password/`, `support/`, `users/me/dashboard/`, `roles/`, `permissions/`, `refresh/`)
- `api/v1/` → `apps.accounts.user_admin_urls` (`users/`, admin CRUD) and `apps.accounts.support_admin_urls`
  (`support-messages/`, admin inbox)
- `api/v1/` → `apps.orders.urls` (`addresses/`, `orders/` — list filters `?store=<id>|manual` and
  `?status=created,preparing` —, `admin/orders/`, `orders/metrics/`,
  `orders/manual/`, `orders/imports/...`, `orders/import-mappings/`)
- `api/v1/audit/` → `apps.audit.urls` (`logs/`, `actions/`, `metrics/`)
- `api/v1/documents/` → `apps.documents.urls` (generated batch output)
- `api/v1/processing/` → `apps.processing.urls` (photo → label-layout agent)
- `api/v1/labels/` → `apps.labels.urls` (`labels/`, `templates/`, `element-layouts/`, `layout-variables/`,
  `admin/`, `render/`, `barcode/`, `batch/`, `fonts/`)
- `api/v1/integrations/` → `apps.integrations.urls` (admin ABM + store connections)
- `api/v1/ingest/` → `apps.integrations.ingest_urls` (API-key/webhook order ingest, separate auth)
- `api/v1/integrations/` → `apps.integrations.label_urls` (Tiendanube Labels API callbacks + public PDF
  download, separate auth — see "Labels the store asks for" below)

REST Framework is closed by default (`DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]`); a public endpoint
needs `permission_classes = [AllowAny]` explicitly. JWT only (`djangorestframework-simplejwt`), access
token in `Authorization: Bearer <token>`.

### Auth & identity (`apps/accounts`)

- **Email is the username** — every login path (Google, email/password, register) normalizes the email
  and uses it as `User.username`.
- `auth_views.py` holds `LoginView`, `RegisterView`, `GoogleAuthView`, `LogoutView`,
  `PasswordResetRequestView`/`Confirm`, `EmailVerificationConfirmView`/`Resend`, `ChangePasswordView`, plus
  `user_payload()`/`auth_response()` — every login endpoint returns the same
  `{access, refresh, user: {..., role, permissions}}` shape.
- Refresh tokens rotate and get blacklisted on rotation/logout (`SIMPLE_JWT` + `token_blacklist` app);
  password reset revokes all outstanding refresh tokens.
- Password rules (`validate_password_strength`, `auth_views.py`): 6+ chars, one uppercase, one digit, plus
  Django's own validators — enforced on register, admin-create, change-password, and reset-confirm.
- `dashboard_views.DashboardView` (`GET users/me/dashboard/`) returns the role-based menu — the frontend
  only renders it. `profile_views.ProfileView` is the self-service `me/` GET/PATCH.

### Roles & permissions (`apps/accounts`)

There is **no `role` field on `User`** — it's derived, never stored directly:

- `permissions_map.get_effective_role()` is the source of truth for "which role": checks canonical Django
  Groups (`admin`, `designer`, `operator`, `subscriber`) first, then falls back to a custom Group name
  (custom roles), then `is_staff → admin`, then `subscriber` (`DEFAULT_ROLE`).
- `GroupRolePermission` (Group ↔ `RolePermission`) is the source of truth for "which permissions", managed
  under `/api/v1/auth/roles/` and `/api/v1/auth/permissions/` (`role_permission_views.py`).
  `permissions_map.ROLE_PERMISSIONS` is only a static fallback for when that table isn't queryable yet.
- `permissions_map.user_has_permission(user, key)` is the single check to call from views;
  `role_permissions.HasRolePermission` wraps it as a DRF permission class. `user_admin_views.UserAdminViewSet`
  calls `user_has_permission` directly per action instead, because different fields need different
  permissions (editing `status` needs `users.reactivate`/`users.deactivate`, not `users.edit`).
- `permissions_map.normalize_role()` maps legacy aliases (`"user"` → `subscriber`, `"administrador"` →
  `admin`, ...); an unrecognized role is treated as a valid custom role and preserved.
- When adding a permission-gated action: add the key to `permissions_map.PERMISSIONS`, seed it via
  `RolePermission` in a migration, gate the view with `user_has_permission`/`HasRolePermission`. Don't add
  a new source of role truth.
- Admin-only permissions (assigned only to the `admin` group): `audit.view`, `orders.view_all`,
  `support.view_all`, `support.manage`, `labels.view_all`, `labels.manage_templates`,
  `documents.view_all` — each seeded by its own `apps/accounts/migrations/00NN_seed_*.py`.

### Audit trail (`apps/audit`)

- `AuditLog` is the single, immutable model (`save()`/`delete()` refuse to touch an existing row): `actor`
  (FK, `SET_NULL`) + `actor_email` snapshot, `category`/`action` (`TextChoices`, one category per action via
  `ACTION_CATEGORIES`), `target_type`/`target_id`/`target_repr` (plain fields, no
  `GenericForeignKey`/contenttypes), a `changes` diff (`{"field": {"from", "to"}}`, never
  passwords/hashes/tokens), IP/user-agent, `created_at`.
- `GET /audit/logs/` (filters: `search`, `category`, `action`, `actor`, `target_type`, `date_from`/`to`,
  `ordering`; permission `audit.view`), `GET /audit/actions/` (catalog for frontend selects), `GET
  /audit/metrics/` (admin activity + login events by month).
- The only way to create a row is `apps.audit.services.record(request=None, *, actor=None, category,
  action, target=None, ..., changes=None)`, called explicitly from the views that need it (no signals).
  It wraps the insert in `transaction.atomic()` and swallows/logs any exception — a failed audit write must
  never break the calling request.

### Orders (`apps/orders`)

- `Address` — free-text `city`/`state` (no catalog), one default per user. `origin` splits the user's own
  address book (`own`) from addresses created by an incoming order (`shipment`: store sync, import, API —
  they belong to a BUYER). `AddressViewSet` (`/api/v1/addresses/`) lists/serves only `own`, so a merchant's
  address book isn't flooded with every buyer's address; the order keeps serving its own address.
- `Order.Status`: created → preparing → dispatched → in_transit → delivered, or cancelled.
  `STATUS_PROGRESS`/`status_rank()` define forward-only movement; `is_shippable`/`CANCELLABLE_STATUSES`
  gate the ship/cancel actions. `source` (web/manual/import/api/webhook/store). Store-order fields:
  `store_connection` (FK, `RESTRICT`), `external_id`/`external_number`, `contact_email`/`phone`,
  `shipping_option`, `package_count`, `total_weight_kg`, `items`, `raw_payload`, `external_updated_at`.
  Idempotency is two conditional unique constraints: `(user, external_id)` when `store_connection` is null,
  `(store_connection, external_id)` otherwise — two stores can both have order "1001".
- `Order.save()` creates an `OrderStatusEvent`, logs `order.status_change` via `apps.audit.services.record`
  (skippable with `_skip_status_audit`, used when a view already logs its own action), dispatches an
  outbound webhook, and calls `integrations.fulfillment.notify_store_shipping_change` for store orders.
- `GET/POST /orders/`, `GET/PATCH /orders/<id>/`, `POST /orders/<id>/ship/` (`orders.create`, forward-only
  status, optional `carrier`/`tracking_number`/`tracking_url`, audited as `order.ship`), `POST
  /orders/<id>/cancel/`. `GET /admin/orders/` (`orders.view_all`) and `GET /orders/metrics/`
  (`orders.view_all`: by month/status, cancellation rate, avg time between statuses, top users, orders by
  city/state). `?store=<id>|manual` filters the caller's own orders by store.
- Manual creation: `POST /orders/manual/` (`orders.create_manual`; `orders.create_for_others` to set
  `user_id`), fields from `ingestion.TARGET_FIELDS`. CSV/Excel import (`orders.import`): `POST
  /orders/imports/` (upload) → `POST /orders/imports/<id>/validate/` → `POST /orders/imports/<id>/confirm/`,
  plus `GET /orders/imports/template/` and saved mappings at `/orders/import-mappings/`
  (`orders.import_mappings`).
- `ingestion.upsert_store_order(connection, normalized)` is the single store-order creation path: keyed on
  `(store_connection, external_id)`, ignores updates older than `external_updated_at`, and
  `_synced_status()` only ever moves a shipping status forward or cancels (never reactivates a locally
  cancelled order) — sets `_skip_store_notification` so the change isn't pushed back to the store that
  reported it.

### Labels — two independent systems (`apps/labels`)

**System 1 — `LabelTemplate`/`Label` + `label_rendering.py`.** The one with a frontend: the editor
(`editor_rotulos.html`) defines the design, the backend validates and renders it. `design` is a dict keyed
by field name (`remitente`, `destinatario`, `domicilio`, `cp`, `localidad`, `pedido`, plus `logo`/`qr`/
`barcode`), each text value `{"left": 0-100, "top": 0-100, "text": optional}` — position in **percent**.
Styled extras (backend-only, editor doesn't write them yet): `font_size`, `bold`, `align`, `width`,
`hide_if_empty`, `shrink_to_fit`, plus decoration keys `texts`/`lines`/`border`.
`label_rendering.build_label_context(order=None, label=None)` builds the print context: `remitente`
(the store's `sender_name`, else its name, else the user), `remitente_domicilio`/`remitente_telefono`
(the store's, empty when unset), `destinatario` (the address' `recipient_name`), `domicilio`, `referencia`,
`localidad`, `cp`, `pais`, `pedido`, `fecha_pedido`, `envio`, `tracking`, `tracking_url`. **A label is stuck
outside the parcel: never print products, prices, payment, or buyer email/phone on it** — only what's
needed to deliver the package. `LabelTemplate` (`owner=None` = system template, `is_public`, `width_cm`/
`height_cm`) and `Label` (`user`, `template`, `order` FK, `client` = recipient, `is_active` soft-delete) are
both served under `/api/v1/labels/` (`labels/`, `templates/`, `admin/` for `labels.view_all`, `render/` for
a one-off PDF, `batch/` for many labels → one `apps.documents.Document`).

**System 2 — `ElementLayout`/`LayoutElement`/`LayoutVariable` + `element_layout_render/`.** A separate,
non-overlapping concept (same file, `models.py`, clearly banner-separated): positions in **millimeters**
(`width_mm`/`x_mm`/...) against a `LayoutVariable` catalog, with its own render pipeline
(`element_layout_render/pdf.py`, `png.py`, `fonts.py`, ...) and its own full CRUD+render API
(`/api/v1/labels/element-layouts/`, `/layout-variables/`) — but **no frontend screen yet**. It exists to
receive the output of the photo-import pipeline below.

### Photo import → label layout (`apps/documents` + `apps/processing`)

Three-step flow (`apps/processing/urls.py` docstring):

1. `POST /api/v1/documents/documentos/` — upload the photo/PDF (`UploadedLabelFile`, `documents.upload`).
2. `POST /api/v1/processing/label-imports/` — `LabelImportViewSet.create` runs `agent.process()`
   synchronously: sends the file to Claude (structured output built from the `LayoutVariable` catalog,
   `schema.py`) asking for element positions as percentages, converts them to mm, and stores the result as
   a reviewable `proposal` JSON on the new `LabelImport` row (`processing.import`; `POST
   /label-imports/<id>/retry/` re-reads the same file).
3. `POST /api/v1/labels/element-layouts/` — the user reviews the proposal and saves it as real
   `ElementLayout`/`LayoutElement` rows. The agent never writes to `apps.labels` directly.

`apps.documents.Document` is a different model: the generated *output* of a label batch (PDF/ZIP), created
by `apps.labels.batch_views.LabelBatchView`, with `status` (processing/ready/failed), `error_message`,
`item_count`, `size_bytes`. `GET/DELETE /api/v1/documents/` (own, soft-delete), `GET .../download/`, `GET
/api/v1/documents/admin/` (`documents.view_all`).

### Store integrations (`apps/integrations`)

The product is an app installed in Tiendanube (Shopify later).

- `StoreConnection` (`platform` + `external_store_id` unique together) is owned by a user (`owner`,
  nullable until claimed), token stored encrypted (`access_token` property, Fernet in `crypto.py`).
  `providers/` has one `StoreProvider` per platform (`get_provider(platform)`); `TiendanubeProvider`
  implements OAuth, `api_request`, webhook signature verification, and order normalization to
  `NormalizedOrder`. Errors: `ProviderError` (retryable) / `ProviderAuthError` / `ProviderNotFoundError` /
  `ProviderRejectedError` (no retry).
- Install: logged-in merchant calls `GET tiendanube/install-url/`; the Partner Portal redirect URL is `GET
  tiendanube/callback/` (public). Installed from the app store (no session), the store lands with
  `owner=None` and the callback redirects with a short-lived `store_claim` token, exchanged via `POST
  stores/claim/`; already-owned stores redirect with `store_connected=<id>`, failures with
  `store_error=<code>`. `POST stores/<id>/disconnect/` revokes (never deletes). `PATCH stores/<id>/settings/` saves how that store's
  labels print — `sender_name`/`sender_address`/`sender_phone`, `logo` (file or the editor's base64 data
  URL, 2 MB cap) and `default_template` (must be public or the caller's) — never token/status; audited as
  `store.update` (the logo logs its filename, not the bytes). Edited per store in `tiendas.html`.
  `batch_views` uses them when printing: the store's logo goes on its orders' labels, and with no
  `template_id` the batch falls back to the store's `default_template` when every order shares it, else
  to the oldest public template. The system template "Etiqueta de envío estándar" prints
  `remitente_domicilio`/`remitente_telefono` with `hide_if_empty`, so a store without them looks exactly
  as before (migration `labels/0010`). All gated by `orders.create`.
- `IntegrationEvent` + `events.py` is a persistent DB queue (no Celery/Redis): webhook receivers only
  verify, `enqueue_event(...)`, and return; `run_integrations_worker` runs handlers registered with
  `@register_handler(platform, event_type)` (`handlers.py`). Exponential backoff up to
  `INTEGRATIONS_EVENT_MAX_ATTEMPTS`; `PermanentEventError` fails without retry; events stuck in
  `processing` past a timeout get requeued.
- `POST tiendanube/webhooks/` (public) verifies the HMAC signature and enqueues; `internal/*` types are
  handler-only, never real webhooks. `order/*` events (`ORDER_SYNC_EVENTS`) fetch the full order and
  `upsert_store_order` it — all orders, not only paid ones. `app/uninstalled` revokes the store.
  `internal/store_setup` registers webhooks then enqueues `internal/import_orders`, which pages through
  the store's recent orders.
- Status sync from the store is forward-only (see `_synced_status` above). Push-back:
  `Order.save()` → `fulfillment.notify_store_shipping_change` → enqueues `internal/push_fulfillment` →
  `TiendanubeProvider.push_fulfillment` PATCHes each fulfillment order's status (forward-only) and
  `tracking_info` only when the tracking code changed.
**Labels the store asks for (`store_labels.py`, `label_views.py`, `label_urls.py`).** The mirror image of
the print flow: instead of the merchant picking orders in *our* app, they tick orders in *their* store
admin and Tiendanube asks us for the labels (Labels API). `POST .../tiendanube/labels/<token>/generate`
(bulk and single are the same endpoint — only the array size changes) has 5 seconds to answer, so it only
stores a `StoreLabelRequest` and enqueues `internal/generate_label`; the worker draws the PDF with the
same `build_shipment_context`/`render_label_pdf` path as everything else and PATCHes the platform back
with `READY_TO_DOWNLOAD` + a `download_url_from_app`. `<token>` is a signed value carrying the connection
id — the callback payload never says which store it is, and it's also the only thing authenticating the
call (Tiendanube documents no signature for these endpoints). The PDF is served without a session at
`.../tiendanube/labels/download/<token>` with a random per-label token, cleared as soon as
`fulfillment_order/label_status_updated` reports `READY_TO_USE`. A label that can't be drawn is reported
as `FAILED` with a reason, and `expire_stale_requests()` (run by the worker each loop) does the same for
anything still pending after `STORE_LABEL_TIMEOUT_SECONDS`, with margin over the 30 minutes Tiendanube
waits before failing it silently. `/cancel` is required by Tiendanube; `/suspension` and `/reactivate`
are optional and share the same contract (`StoreLabelDecisionView`) — for us all three mean "stop serving
the PDF".
- **Carrier registration is manual, on purpose.** `python manage.py register_store_carrier [<store id>]`
  calls `store_labels.register_carrier`, which needs `STORE_LABEL_RATES_URL` — Tiendanube requires a
  *rate-quoting* `callback_url` alongside the labels one, so from the moment the carrier exists the store
  asks us for shipping prices at every checkout. That endpoint does not exist yet (it is the open product
  decision), so nothing registers a carrier automatically at connect time.
- **Plan gating.** `connect_store` saves the store's `features` into `StoreConnection.preferences`, and
  `store_labels.supports_label_api()` reads `fulfillment_order_label_api` off it (`None` = unknown, for
  stores connected before this existed). Surfaced as `label_api_enabled` on the store serializer.
- **The merchant's view.** `GET /api/v1/integrations/store-labels/` (`orders.create`, read-only, filters
  `?store=` and `?status=`) lists the caller's own store label requests with the failure reason — never
  the stored `payload` (buyer data) nor the download token. `rotulos_tienda.html` +
  `assets/js/rotulos_tienda.js` render it, linked from `tiendas.html` and the `store_labels` menu item.

- Privacy webhooks (`privacy.py`, work for revoked stores too): `customers/redact` anonymizes the matched
  orders, `store/redact` revokes the store and anonymizes everything, `customers/data_request` emails a
  JSON report to the store owner (no owner → fails without retry, report stays in `event.result`). Orders
  are anonymized, never deleted.

### Frontend

Plain multi-page app, one HTML file per screen, no framework/bundler. Admin pages
(`gestionuser.html`/`roles.html`/`reportes.html`/`auditoria.html`/`pedidos_admin.html`/`soporte_admin.html`)
share the same script order — `config.js`, `auth.js`, `utils.js`, `admin_common.js`, `admin_sidebar.js`,
then the page's own script — and each redirects to `dashboard.html` if the caller lacks that page's
permission (UI-only gating; the backend re-checks every permission server-side).

- `assets/js/utils.js` — shared helpers loaded as a classic script (global functions, not an ES module):
  `escapeHtml`, `getErrorMessage`, `showMessage`, `formatDate`, `extractResults`.
- `assets/js/topbar.js` — the shared top bar for the non-admin pages (avatar, name, email, per-page links
  and the `logoutBtn` button). The page declares only
  `<header class="topbar" id="appTopbar" data-links='[{"href":"…","label":"…"}]'></header>`; the script
  fills it from the stored session on load, and a page holding a fresh user calls
  `window.AppTopbar.render(user)`. Each page still wires its own `logoutBtn` listener. `dashboard.html` and
  the admin pages keep their own header (admin ones come from `admin_sidebar.js`).
- `assets/css/base.css` — reset, `body`/`.layout`/`.main`, top bar and buttons, loaded BEFORE the page's own
  stylesheet by the pages whose CSS had these rules byte-identical (`documentos`/`importar`/`integraciones`/
  `labels_shared`/`pedidos`/`perfil`). Color tokens (`:root`) stay per file — not every screen uses the same
  palette, and `tiendas.css`/`despachar.css`/`gestionuser.css` keep their own variants and don't load it.
- `pedidos.html` paginates its order history (`?page=`, Anterior/Siguiente driven by the API's `next`).
- `assets/js/admin_common.js` — permission/formatting helpers for admin pages: `isAdminMode()`,
  `canUseUserPermission()`, `canViewUsers()`, `translateRole()`, `actionLabel()`, `formatDateTime()`,
  `renderSimplePager()`, `loadAuditActionsCatalog()`.
- `assets/js/auth.js` — `window.Auth` (`apiFetch`, `getAccessToken`, `getCurrentUser`, `logout`).
  `apiFetch` attaches the Bearer token, retries once through a single shared refresh on 401, and on
  failure clears the session and redirects to `index.html` (throwing an error with `.isSessionExpired`).
- `assets/js/labels_api.js` — the only CRUD client for `apps.labels` (System 1): a real ES module,
  dynamically `import()`-ed from `saved_labels.js`/`templates.js` and used directly by `editor_rotulos.html`.
- `imprimir_rotulos.html` + `assets/js/print_labels.js` — the merchant's print flow: filter the caller's
  orders by store and status, tick them (the selection survives paging and filter changes), pick a
  template (default: each store's own) and layout, then `POST /api/v1/labels/batch/` with `order_ids`
  and land on `documentos.html` to download. `skip_existing` is on by default; an optional
  "mark as dispatched" runs `POST /orders/<id>/ship/` per order afterwards, and orders that can't be
  shipped are reported without breaking the rest. Linked from `pedidos.html` and from the
  `labels_print` menu item (`labels.batch`).
- Orders that come from a store carry third-party text (buyer name/address, store name): any value
  inserted with `innerHTML`/`insertAdjacentHTML` must go through `escapeHtml` or use `textContent` instead.
- `google.js`/`google_test.js`/`assets/apis/access.php`/`index_test.html` are unused leftovers from a PHP-era
  Google-login prototype — not part of the real flow (`index.html` calls `GoogleAuthView` directly). Don't
  delete them. Their dependencies (`assets/apis/vendor/`, ~500 MB) are gitignored and regenerate with
  `composer install` from `assets/apis/composer.json`.
- No frontend screen exists yet for System 2 (`ElementLayout`) or the photo-import pipeline
  (`apps.processing`) — `documents`/`processing` menu items stay disabled in `DashboardView`.

## Reglas

- **Idioma**: todo lo que es programación va en inglés — archivos `.py`/`.js`/`.css`, clases, funciones,
  variables, endpoints, tests. Lo que ve el usuario va en español — textos, mensajes de error, labels de
  choices. Los `.html` pueden tener nombre en español pero sin ñ ni acentos. **Nunca se renombran datos ya
  guardados**: claves del JSON `design` (`remitente`/`destinatario`/...), variables `{{...}}`, valores de
  choices (`"documento.create"`), claves de permiso (`plantillas.view`) quedan como están aunque no sigan
  esta regla.
- **Frontend: una página por función.** Cada pantalla/función nueva va en su propio `.html` con su propio
  `assets/js/<pagina>.js`, nunca como otra sección oculta dentro de una página existente. No reorganizar
  las páginas grandes existentes sin pedirlo.
- **Funciones compartidas del frontend van en `assets/js/utils.js`** — no copiarlas en cada página.
- Después de crear una migración, correr `python manage.py migrate` sobre la base local además de probarla.
- Nunca ejecutar `git pull`, `git push` ni `git commit`. Solo copia local.
- Nunca modificar tests para que pasen: corregir la implementación.
- Nunca usar URLs placeholder. La API real es `http://127.0.0.1:8000/api/v1/`.
- No borrar archivos de Google/OAuth (`google.js`, `google_test.js`, `access.php`, `oauth-test/`,
  `index_test.html`) aunque parezcan código muerto.
- Antes de modificar: inspeccionar el código, identificar la causa exacta, cambio mínimo. Nada de
  refactors grandes para problemas chicos.
- Si el problema es de backend no tocar el frontend, y viceversa.

## Arquitectura

- Validación, permisos, reglas de negocio y autorización van en el backend. El frontend solo presenta.
- Identidad siempre desde `request.user` (JWT), nunca desde un ID que manda el cliente.
- Roles: admin, designer, operator, subscriber. Legacy `"user"` = `"subscriber"`. Admin = grupo `admin` +
  `is_staff`.
