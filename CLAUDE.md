# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

ROTULOS_PERSO is a **multi-client** shipping-label ("rótulo") app, distributed as an app installed in
online stores (Tiendanube, Shopify, WooCommerce, VTEX, Magento and Empretienda): each client connects their store, their orders
arrive automatically, and the app generates the labels, dispatches and pushes tracking back. It is **not**
built for a single company — never hardcode a client, sender or carrier name as a default. A "rótulo" is a
shipping/waybill LABEL stuck on a parcel — not a product tag — with sender (the client/store that ships),
recipient (the buyer), address, postal code, city/province, order number, QR and barcode.

Backend: Django REST API (`backend/`), all routes under `/api/v1/` (`backend/config/urls.py`). Apps:
`accounts` (auth, user admin, roles/permissions, support inbox), `orders` (addresses/orders, manual
creation, CSV/Excel import, store sync, dispatch), `audit` (read-only trail), `labels` (two independent
label-rendering systems, see below), `integrations` (store connections, webhooks, event queue),
`documents` (generated batch output + uploaded source files), `processing` (Claude vision agent that
reads a photographed label) and `carriers` (shipping companies the clients dispatch with: Andreani). Frontend: static multi-page app (`frontend/`), plain HTML/CSS/JS, no build
step.

Don't assume: there is no point-of-sale/destination catalog (`Address` stores city/state as free text),
the only carrier API is Andreani's (`apps/carriers`, see below; for anything else `Order.carrier`/
`tracking_number` are typed in when dispatching), and the app is not scoped to one client or one carrier.

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
- One platform folder: `python manage.py test apps.integrations.providers.vtex` (or `.magento`)
- Migrations: `python manage.py makemigrations` / `python manage.py migrate`
- Django shell: `python manage.py shell`
- Store integrations worker (processes `IntegrationEvent`, retries): `python manage.py run_integrations_worker`
  (`--once` for a single batch, `--limit`, `--sleep`)

`DJANGO_SETTINGS_MODULE` selects `config.settings.dev` or `config.settings.prod`; both import from
`config/settings/base.py`. `manage.py` defaults to `dev`.

**Logging** is configured in `base.py`, not only in prod. Everything under the `apps.` namespace goes to
a formatted console handler at `LOG_LEVEL` (default `INFO`; `prod.py` starts it at `WARNING`), and the
`django` logger is declared with `propagate: False` so its records don't come out twice — once bare from
Django's own handler and once formatted from the root one. This matters because `apps.integrations` is
written to **log instead of raising** (an error there must not break a buyer's checkout), and that choice
is only safe if someone can read the logs: before, outside production there was no configuration at all,
so Python fell back to `lastResort` — every `INFO` was dropped in silence, including
the rate callbacks' (`providers/tiendanube/rates.py`, `providers/woocommerce/rates.py`) "no rate for this postal code", which is the diagnostic for why a store's shipping
option never appeared.

### Health probes (`config/health.py`)

Two, not one, and the split is the point. `GET /api/v1/health/` is **liveness**: it touches nothing and
answers "the process is up" — that is what an orchestrator polls to decide whether to restart the
container, so putting a DB query in it would restart the API on every Postgres hiccup, exactly when the
API is not the problem. `GET /api/v1/health/ready/` is **readiness**: it runs a real `SELECT 1` and
returns **503** when the database is unreachable, which is the one worth monitoring. Before, a single
probe returned `{"status": "ok"}` without touching anything, so it kept reporting healthy with the
database down.

### Deploying (`docker-compose.prod.yml` + `Caddyfile`, repo root)

Brought up **by hand** — there is no pipeline and none is needed yet; deploying and automating the deploy
are different things, and the second only earns its keep after doing the first a few times. Five
services: Postgres, the API (migrations + `collectstatic` then gunicorn), the integrations worker, the
frontend, and Caddy, which terminates TLS with an automatic Let's Encrypt certificate — Tiendanube
refuses HTTP callbacks, so the certificate is not optional. Only Caddy publishes ports; the database is
not exposed at all, unlike the dev compose.
- Because Caddy serves the frontend and the API under **one domain**, nothing is cross-origin and CORS
  stops mattering in this layout.
- **The API healthcheck sends `X-Forwarded-Proto: https`** on purpose: `prod.py` forces
  `SECURE_SSL_REDIRECT`, and the probe goes over plain HTTP to the container itself, so without that
  header Django answers 301 and the container would be marked unhealthy while being perfectly fine.
- `frontend/Dockerfile` is nginx with no build step (there is nothing to compile). Its entrypoint
  **generates `assets/js/config.js` from `API_BASE`** at container start: that file hardcodes
  `127.0.0.1:8000` for dev, which on a server would make the merchant's browser call *their own*
  machine. `.dockerignore` leaves out `assets/apis/` — 502 MB of the 504 the folder weighs, and nginx
  does not run PHP anyway — so the image is ~50 MB.
- Media (`MEDIA_ROOT`) is a real volume: the generated batch PDFs and store logos live on disk, so a
  platform with an ephemeral filesystem would lose them on every redeploy.
- The images use fully-qualified names (`docker.io/library/...`) so Podman resolves them too. Note the
  laptop has `docker-compose` 1.29.2, which is too old for these files (and for `backend/docker-compose.yml`
  as well) — Compose v2 (`docker compose`, no hyphen) is required.

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
lives only in `assets/js/config.js` (`window.APP_CONFIG.API_BASE`), loaded first on every page; every other
script builds URLs on it. Opened from `localhost`/`127.0.0.1`/`file://` it is `http://127.0.0.1:8000/api/v1`;
from any other host it is that same origin's `/api/v1` — the dev tunnel publishes `/api`, `/media`, `/admin`
and `/static` → 8000 and `/` → 8001 under one domain, so someone else's browser can use the app (a hardcoded
127.0.0.1 would make it call *their* machine). The Docker image still overwrites the file from `API_BASE`.

## Architecture

### Backend URL map (`config/urls.py`)

- `api/v1/auth/` → `apps.accounts.urls` (login/register/Google/logout/password-reset/verify-email, `me/`,
  `me/change-password/`, `support/`, `users/me/dashboard/`, `roles/`, `permissions/`, `refresh/`)
- `api/v1/` → `apps.accounts.user_admin_urls` (`users/`, admin CRUD) and `apps.accounts.support_admin_urls`
  (`support-messages/`, admin inbox)
- `api/v1/` → `apps.orders.urls` (`addresses/`, `orders/` — list filters `?store=<id>|manual`,
  `?status=created,preparing` and `?date_from=`/`?date_to=` (YYYY-MM-DD, both inclusive, by creation
  date) —, `admin/orders/`, `orders/metrics/`,
  `orders/manual/`, `orders/imports/...`, `orders/import-mappings/`)
- `api/v1/audit/` → `apps.audit.urls` (`logs/`, `actions/`, `metrics/`)
- `api/v1/documents/` → `apps.documents.urls` (generated batch output)
- `api/v1/processing/` → `apps.processing.urls` (photo → label-layout agent)
- `api/v1/labels/` → `apps.labels.urls` (`labels/`, `templates/`, `element-layouts/`, `layout-variables/`,
  `admin/`, `render/`, `barcode/`, `batch/`, `fonts/`)
- `api/v1/integrations/` → `apps.integrations.urls` (admin ABM + store connections, plus
  `<platform>/install-url|callback|webhooks/` per provider and `shopify/launch/`)
- `api/v1/carriers/` → `apps.carriers.urls` (`andreani/account/`, `andreani/branches/`, `andreani/shipments/`...)
- `api/v1/ingest/` → `apps.integrations.ingest_urls` (API-key/webhook order ingest, separate auth)
- `api/v1/integrations/` → `apps.integrations.providers.tiendanube.label_urls` and `.rate_urls` (Tiendanube Labels API callbacks + public PDF
  download, separate auth — see "Labels the store asks for" below)

REST Framework is closed by default (`DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]`); a public endpoint
needs `permission_classes = [AllowAny]` explicitly. JWT only (`djangorestframework-simplejwt`), access
token in `Authorization: Bearer <token>`.

### Shared across apps (`apps/common`)

A plain package, not a Django app (no models, not in `INSTALLED_APPS`). `date_filters.py` holds the
`?date_from=`/`?date_to=` contract (AAAA-MM-DD, **both inclusive**): `parse_date_param`,
`parse_date_range` and `date_range_q(query_params, field="created_at", ...)`, which returns a `Q` — empty
when no dates came, so a caller filters with it unconditionally. Seven views across six apps each carried
their own copy, five of them identical character for character, with two different error messages and one
endpoint that silently accepted a reversed range. The comparison is always `__date__gte`/`__date__lte` on
a `DateTimeField`: with a plain `__lte`, "up to the 24th" would exclude everything on the 24th except
exact midnight. It reads `.get()`, so it takes query params or a JSON body dict (`batch_views` passes
`filters` from the body).

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
- **Bulk actions (`bulk_views.py`, logic in `bulk.py`/`manifest.py`)** — each processes orders one by one
  and answers `{updated, failed, results: [{order_id, number, ok, detail}]}`, so one order that can't move
  (delivered, cancelled, foreign) never blocks the rest. They reuse `shipping.apply_shipping`/`apply_cancel`,
  the same code as `ship`/`cancel` (forward-only status, store push via `Order.save`, `order.ship`/
  `order.cancel` audit with actor), and the permissions of the one-by-one action — no new permission keys.
  Max 500 orders per call.
  - `POST /orders/bulk-status/` `{order_ids, status, carrier?}`: `cancelled` needs `orders.cancel`, the
    shipping statuses `orders.create`.
  - `GET /orders/export/?file_type=csv|xlsx` + the list's own filters (`views.filter_orders`, shared with
    `GET /orders/`) or `ids=`. **`file_type`, not `format`**: DRF reserves `?format=` for the renderer and
    404s on "csv". CSV is `;` + BOM (what Spanish Excel opens on double click); third-party text starting
    with `= + - @` is neutralized (CSV injection) and xlsx cells are forced to string. Count in
    `X-Order-Count` (exposed via `CORS_EXPOSE_HEADERS`, like `Content-Disposition`).
  - `POST /orders/manifest/` `{order_ids, carrier?}` → "Planilla de retiro" PDF (platypus table with
    repeated header + signature block kept together) the carrier signs on pickup. Same rule as the label:
    no products, prices or buyer contact. Changes no status.
  - `POST /orders/tracking-import/preview/` (multipart `file`, optional `mapping` JSON, `carrier`,
    `store`) reads the carrier's spreadsheet with `import_parsing`, guesses columns by synonym
    (`detect_columns`: tracking before order, because "número de seguimiento" contains "número") and
    matches each row to an own order — `external_number`, then `external_id`, then our id — with a
    per-row `state` (`ok`/`not_found`/`ambiguous`/`not_shippable`/`duplicate`/...). Stores nothing.
    `ambiguous` = two stores share the number; `store` breaks the tie. `.../confirm/` `{status, rows}`
    applies only the rows sent back; an order already further along keeps its status and only gets the
    tracking.
- `ingestion.upsert_store_order(connection, normalized)` is the single store-order creation path: keyed on
  `(store_connection, external_id)`, ignores updates older than `external_updated_at`, and
  `_synced_status()` only ever moves a shipping status forward or cancels (never reactivates a locally
  cancelled order) — sets `_skip_store_notification` so the change isn't pushed back to the store that
  reported it.

### Labels — two independent systems (`apps/labels`)

**System 1 — `LabelTemplate`/`Label` + `label_rendering.py`.** The one with a frontend: the editor
(`editor_rotulos.html`) defines the design, the backend validates and renders it.

**The editor canvas mirrors the renderer, and the renderer has the last word.** The canvas used to be CSS
in fixed pixels (`font-size: 11px`, logo 64px, QR 72px) while `render_label_pdf` scales with the label, so
printed text came out ~1.8× the size shown on a 10×15cm label — and the gap grew with the label. Now
`applyRenderMetrics()` computes the same numbers as `draw_label_page`: `max(6, height_cm * (72/2.54) *
0.035)` pt for text, `max(1, min(w,h) * 0.22)` cm for the logo box, 3cm for the QR and 8×1.5cm for the
barcode (both shrunk by the `_fit_size_cm` rule), fields anchored with no padding (outline, not border),
`line-height: 1`, and no wrapping because the renderer truncates with an ellipsis. **If those constants
change in `label_rendering.py`, change them in the editor too.** `renderDecorations()` also draws the
`border`/`lines`/`texts` the renderer adds, and `applyRenderMetrics` honours per-field `font_size`/`bold`/
`align`/`width` — the seeded template uses all of them, and none of it used to show.
- The safety net is `POST /api/v1/labels/preview/` (`PreviewLabelView`, `labels.render`): it renders a
  design that has **not been saved** — `{design, width_cm, height_cm, template_id?}` — with
  `build_preview_context()`'s deliberately long sample values, and returns the PDF. The editor's "Ver cómo
  sale impreso" shows it in a modal. When the canvas and the PDF disagree, the PDF is right.
- The **field properties panel** (`fieldPropsGroup`) edits those per-field styles: click a text field on
  the canvas and set `font_size`, `bold`, `align`, `width`, `hide_if_empty`, `shrink_to_fit`. It follows
  the same select-then-configure pattern as the QR/barcode panel beside it. An empty or unchecked control
  **deletes** the key instead of storing a falsy value, so the design keeps only what the user actually
  chose and the renderer's defaults (proportional size, full width to the right edge) stay in force.
- `collectFields()` re-emits the styles and the decorations. Before, opening a template and saving it
  silently stripped its border, rules and fixed texts.
- The **decorations panel** (`decoGroup` + `decoPropsGroup`) creates them: a "Recuadro" checkbox (plus
  "Punteado"), `+ Línea` and `+ Texto fijo`. A line or a fixed text is selected by clicking it on the canvas
  and dragged from there — the drag writes into `decorations` and re-renders, because the decoration layer
  is rebuilt on every render and a position left in the DOM would be lost. A line only moves vertically:
  its horizontal extent is `left`/`right`, typed in so two rules line up exactly rather than nearly.
  `syncDecoToolbar()` ticks the border boxes from the design when a template is opened.
- **The decoration preview used to lie.** The editor drew the border from `margin`/`width` and the lines
  from `width`/`thickness` — keys `_draw_decorations` ignores. It uses a fixed `BORDER_INSET_CM` (0.2 cm),
  `setLineWidth(0.6)` for both, and `left`/`right` (defaults 4 and 96) for lines. So a 10×15 label showed a
  border flush against the edge and rules 4% too long. `renderDecorations()` now mirrors those constants;
  **if they change in `label_rendering.py`, change them here too.** `design` is a dict keyed
by field name (`remitente`, `destinatario`, `domicilio`, `cp`, `localidad`, `pedido`, plus `logo`/`qr`/
`barcode`), each text value `{"left": 0-100, "top": 0-100, "text": optional}` — position in **percent**.
Styled extras: `font_size`, `bold`, `align`, `width`,
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

**ZPL for thermal Zebra printers (`zpl.py`).** The same `design`, emitted as the printer's native
language instead of a rasterized page — a second drawing path, not just another export, which is why it
is its own module. Text and codes become firmware commands (`^A0`/`^FB`, `^BQ`, `^BC`/`^BE`), so output
is crisp and fast and needs no driver. **The PDF stays the path that always works**: a printer this
doesn't cover prints the PDF and nobody is left unable to ship.
- **Coordinates are dots from the top-left**, which is the editor's own `left`/`top` model, so unlike
  `percent_to_canvas_xy` there is no Y inversion. How many dots fit in a centimetre depends on the model
  (`dpmm`: 8 = 203 dpi, the bulk of the fleet; 12 = 300 dpi), so it is a parameter, defaulting to 8.
  `^MU` (Zebra's command for rescaling a format written for another resolution) is deliberately **not**
  used: the ZPL is generated per request, so the right dots are computed directly. `^MU` is for fixed
  templates that can't be regenerated.
- **The printer measures the text, not us** (`^FB` with one line): ZPL font 0's metrics aren't
  Helvetica's, so any width we computed would be a guess. `shrink_to_fit` is the exception and is
  explicitly an approximation. **`bold` is faked by printing the field twice one dot apart** — font 0 has
  no bold variant, and ignoring it would silently flatten the design's hierarchy.
- `^CI28` (UTF-8) is mandatory, or "María" prints as garbage — the failure that never shows up in an
  English test. `^` and `~` in the data are escaped as hex through `^FH`, since a tracking URL with a `~`
  would otherwise turn into commands.
- **A dash is drawn as a run of short `^GB`s**: ZPL has no dashed stroke. The logo becomes a 1-bit
  dithered `^GF`; an RGBA image is composited onto white first, or the transparent background burns as a
  black rectangle.
- **Exposed at** `POST /labels/render/` (`format: "pdf"|"zpl"`, `dpmm`) and `POST /labels/batch/`
  (`output: "pdf"|"zip"|"zpl"`), where `page_layout` is ignored — there is no sheet to fill, the roll is
  already die-cut, so nothing like the A4 grid applies. `imprimir_rotulos.html` folds all of it into one
  "Formato" select, because a merchant knows their printer, not what a dpmm is.
- **The density is a per-store setting**, not a question asked on every print: `_resolve_dpmm` mirrors
  `_resolve_template` — an explicit `dpmm` wins, otherwise the store's `label_printer_dpmm` when *every*
  order in the batch comes from that same store, otherwise 203 dpi. A batch mixing stores with different
  densities has no right answer, so it falls back rather than picking one and printing half the labels at
  the wrong scale.
- **Verified against a real ZPL engine**: `test_zpl.LabelaryRenderTests` renders through
  api.labelary.com at both densities, and is skipped unless `ZPL_LABELARY_TESTS=1` — the suite can't
  depend on someone else's service, but it means a printer isn't needed to check the output.

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

The product is an app installed in the merchant's store platform: Tiendanube (complete), Shopify
(connection, orders, pushing the dispatch + tracking back, printing from its admin), WooCommerce
(phase 1: connection by both paths, orders, periodic reconciliation, dispatch + tracking note) and VTEX
(written without an account yet: key connection, hook + feed, tracking on the invoice) and Magento
(phase 1, written without a store: Integration credentials, reconciliation, shipment + tracking) and
Empretienda (no API: its exported sales spreadsheet is imported).

- **Every platform lives in its own folder** (asked 2026-10-08, so it can be found and fixed by hand):
  `providers/<platform>/` holds its `provider.py`, its own views/urls/handlers/modules, and `tests/`;
  the frontend part is in `frontend/assets/js/<platform>/`. Each folder's `__init__.py` lists its files
  and what has to live elsewhere: the `Platform` choice and migrations, settings, the management
  command `register_store_carrier`, the HTML forms/pages (`imprimir_tiendanube.html` stays at the root,
  its URL is registered in the Partner Portal) and `rotulos-extension/` (the Shopify CLI project). What
  several platforms share stays at `apps/integrations/` level: `store_print.py` (signed print link,
  order resolution, PDF — used by Shopify, Woo and Tiendanube), `shipping_rates.py` (the rate table and
  its rule), `views.py` (OAuth install/callback, webhooks receiver, `_print_document`,
  `_record_store_connect`), `handlers.py` (which imports `providers/tiendanube/handlers.py` at the end
  to register Tiendanube's label handlers), `stores.py`, `privacy.py`, `tokens.py`. Test helpers shared
  by every platform are in `apps/integrations/tests/helpers.py`. Platform JS loads AFTER `tiendas.js`
  and is only called from event/async paths. **A new platform starts directly in its folder.**
- **Nothing outside `providers/` names a platform** (except the Tiendanube-only Labels API, rates and
  carrier). Whatever differs lives on the `StoreProvider` (`providers/base.py`) as an attribute or method:
  `order_sync_events`/`uninstall_events`/`extra_webhook_events`/`privacy_events` (which webhook names
  mean what), `parse_webhook` (Tiendanube puts store+event in the body, Shopify in headers),
  `verify_callback`/`callback_shop_domain`/`requires_shop_domain`/`requires_oauth_state` (OAuth
  differences), `fulfillment_status_for` (local status → platform's shipping status),
  `supports_order_import`, `refresh_access_token`. `handlers.py` registers the generic handlers for
  every `all_providers()` entry with the provider's own event names, and `urls.py` generates
  `<platform>/install-url|callback|webhooks/` per provider (fixed paths, so reverse names like
  `tiendanube-webhooks`/`shopify-callback` exist and an unknown platform is a 404). Adding a platform =
  a provider + a `Platform` choice; not new views.
- `StoreConnection` (`platform` + `external_store_id` unique together) is owned by a user (`owner`,
  nullable until claimed), tokens stored encrypted (`access_token`/`refresh_token` properties, Fernet in
  `crypto.py`; `set_tokens(OAuthResult)`/`clear_tokens()` keep them and their expiries consistent).
  `providers/` has one `StoreProvider` per platform (`get_provider(platform)`); `TiendanubeProvider`
  implements OAuth, `api_request`, webhook signature verification, and order normalization to
  `NormalizedOrder`. Errors: `ProviderError` (retryable) / `ProviderAuthError` / `ProviderNotFoundError` /
  `ProviderRejectedError` (no retry).
- **Tokens that expire (`tokens.py`).** Tiendanube's token never expires (`token_expires_at` null, used
  as is). Shopify's does, after an hour, and that is mandatory: new public apps must request expiring
  offline tokens (`expiring=1`) since 2026-04-01, and all public apps from 2027-01-01. `access_token_for`
  refreshes 60 s before expiry, and `ShopifyProvider.graphql` forces one refresh + retry on a 401. The
  refresh runs with the row locked (`select_for_update`) because **refresh tokens rotate**: two
  processes refreshing at once would leave one holding an invalidated refresh token; the second one sees
  the ciphertext changed and uses the new token.
- **Shopify (`providers/shopify/provider.py`)**: `external_store_id` is the shop domain (`xxx.myshopify.com`,
  validated with an anchored regex — `normalize_shop_domain` also accepts `mitienda` or a pasted URL).
  GraphQL Admin API only (REST is closed to new public apps). The OAuth callback and the App URL
  (`GET shopify/launch/`, where Shopify sends a merchant who installs or opens the app) carry an `hmac`
  over the query string; webhooks are signed in base64 in `X-Shopify-Hmac-Sha256` with topic/shop in
  headers. Every Shopify install passes through us first, so the callback **requires** a `state`: the
  launch view signs one with no user, bound to that shop (`make_oauth_state(None, "shopify", shop)`),
  and the store ends in the claim flow. **Reading orders needs "protected customer data" access** requested
  in the Dev Dashboard (API access requests → protected customer data + the Name and Address fields;
  on a development store selecting them is enough, no review). Without it Shopify answers "This app is
  not approved to access the Order object" — and `ordersCount` returns 0, which looks like an empty
  store. Only name and address are queried, never the buyer's email/phone: the label never prints them.
  Orders are GraphQL `Order` nodes keyed by `legacyResourceId` (the numeric id webhooks and
  `orders_to_redact` use); `address1` is split into street + number (`split_street`, the last token
  starting with a digit: "Calle 12 1500" → "Calle 12" + "1500"). A leading number is never split
  off: the label prints street + number in that order, so "105 Victoria St" would come out
  "Victoria St 105", and in Argentina "25 de Mayo" is a street name. A shipping address with no name (a customer typed in the admin) falls back to the
  billing address' name. The import pages by cursor
  (`list_orders_page` → `OrdersPage.next_cursor`; the base implementation keeps Tiendanube's numbered
  pages). An `orders/*` webhook is stored in the queue as just `{id, updated_at}` — the worker refetches
  the order, so the buyer's data isn't kept twice. Registered webhooks: `orders/create|updated|cancelled`
  and `app/uninstalled`. Dispatching (`push_fulfillment`) creates a Shopify fulfillment per fulfillment
  order the app is allowed to fulfill (`CREATE_FULFILLMENT` in `supportedActions` — a third-party
  fulfillment service's orders don't even show up with our scopes), with `trackingInfo`
  `{number, url, company=Order.carrier}`, and rewrites tracking only when the number changed, on the
  fulfillments reached THROUGH those visible fulfillment orders — never via `order.fulfillments` +
  `Fulfillment.service`: reading `service` needs scopes the app lacks, and Shopify rejects the whole
  query as soon as the order has any fulfillment (found live; the schema validator doesn't catch it). **In transit and delivered are pushed as fulfillment events** (`fulfillmentEventCreate`, status
  `IN_TRANSIT`/`DELIVERED`) on each of the app's fulfillments, including one created in the same push
  (an order marked delivered without being dispatched first gets fulfilled, then delivered). Skipped
  when the fulfillment's `displayStatus` is already there or further on (`_ALREADY_AT`): no duplicate
  line in the buyer's timeline, and a delivered shipment never goes back to in transit. It needs
  `write_fulfillments` (added 2026-10-05): a store installed before that has it missing from
  `StoreConnection.scopes`, so the event is skipped without failing (the shipment itself went out), and
  `ShopifyLaunchView` sends that merchant back through OAuth the next time they open the app
  (`_needs_new_scopes`; Shopify only asks for what's missing and the callback updates the same row).
  Not for empty `scopes` (unknown — it would loop) nor for own-app connections. `missing_scopes`
  treats a granted `write_x` as covering `read_x`.
- **Bulk actions from the store's own admin = three actions over the print-link flow** (2026-10-09, user:
  "agregá todas las acciones masivas posibles"). `POST <platform>/print-link/` takes `action`:
  `labels` (default, as before), `manifest` (planilla de retiro, `apps.orders.manifest`, carrier filled when
  all orders share one) or `dispatch` (Andreani). Shared in `store_print.py`: `parse_action`,
  `action_link(connection, orders, action, route)` → `(url, summary)` and `render_document(token, platform)`
  (what `_print_document` serves; the token carries `a` and, for dispatch, the shipment ids `s`).
  **Dispatching happens in the POST, never in the GET of the link** (it creates Andreani shipments); the
  link only prints them (`andreani/printing.bundle`: rótulo + Andreani label). `andreani/store_dispatch.py`
  picks the contract by itself — the store's `andreani_checkout.contract` if it's a home contract, else
  the account's first home one (branch contracts need a branch per order: only from Mis pedidos) —, reuses
  an order's open shipment instead of creating another, and returns `failed: [{number, detail}]`; no
  Andreani account → 400 "Conectá tu cuenta de Andreani…". Max `MAX_ORDERS_PER_DISPATCH` (30) per action.
  Cambiar estado / Exportar were left out on purpose: every store already has them and the store owns the
  status. Not available in Magento/VTEX (no admin module of ours yet) nor Empretienda (no API). Per platform:
  WooCommerce plugin 1.3.0 (three bulk actions, dispatch with a 90 s timeout, failures shown as an admin
  notice); Tiendanube = one Partner Portal link per action, each to its page (`imprimir_tiendanube.html`,
  `planilla_tiendanube.html`, `despachar_tiendanube.html`, same JS with `<body data-action>`; the user
  must register the two new links); Shopify = a second Print option (`rotulos-planilla`) and an admin
  action (`rotulos-despachar`, target `admin.order-index.selection-action.render`, confirms with a button)
  in `rotulos-extension/` — needs `shopify app deploy` (the CLI adds their `uid`). Tests:
  `apps/integrations/tests/test_store_actions.py`.
- **Printing labels from Shopify's own admin (`providers/shopify/admin_print.py` + the shared `store_print.py`).** The merchant ticks orders in their
  Shopify order list → Print menu → our labels. That menu entry is an *admin print action extension*
  (target `admin.order-index.selection-print-action.render`), a separate Shopify CLI project in
  `rotulos-extension/` (extension `extensions/rotulos-envio/`) — the only part of the product that needs the CLI; the app itself stays Django and
  was created by hand. Two steps on purpose: `POST shopify/print-link/` is called by the extension with
  `fetch` and an **ID token** it attaches itself (`shopify.auth.idToken()`; JWT HS256 with the client
  secret, `aud` = client id, `dest` = the shop — `verify_id_token`). The extension calls a **relative** `/api/...` URL, which Shopify
  resolves against the App URL in `shopify.app.rotulos-perso.toml` (verified live) — so the extension
  carries no server domain and only that TOML changes with the server. **Don't test it from the laptop
  that runs the Tailscale tunnel** with Tailscale DNS on: there the domain resolves to the tailnet's
  private IP (100.x) and the browser's local-network-access protection kills the request from Shopify's
  public page — NetworkError, status null, nothing in the server log (`tailscale set
  --accept-dns=false` while testing, or use another device). The endpoint resolves the selected orders (fetching and upserting
  any we don't have yet, so a chosen order can't be missing from the PDF), and returns a signed,
  short-lived link (`SHOPIFY_PRINT_LINK_MAX_AGE_SECONDS`, 15 min). `GET shopify/print/<token>` serves the
  PDF (store template + logo, same renderer as the batch) to Shopify's print preview, which loads it
  as a document — no ID token there, and it's framed, hence `xframe_options_exempt`. CORS is opened
  for `print-link/` and `print/<token>` ONLY, from any origin, via corsheaders' `check_request_enabled`
  signal (`apps.py`) — the print preview also fetches the PDF with a CORS preflight (seen in the logs;
  dev's allow-all hid it). Both are authenticated by Shopify's token or the signed link, not a cookie. The privacy topics
  (`shop/redact`, `customers/redact`, `customers/data_request`) are declared in the app config, not via
  API, and reuse `privacy.py`. Listing in the Shopify App Store would additionally require an embedded
  app (App Bridge) and Shopify's Billing API — not done. Settings: `SHOPIFY_CLIENT_ID`/`SECRET`/
  `API_VERSION`/`SCOPES`; the redirect URL is built from `INTEGRATIONS_PUBLIC_BASE_URL`.
- **Shopify manual connection (`POST shopify/connect-manual/`, `stores.connect_with_own_app`).** For a
  merchant who can't install our app: they create their OWN app in their Dev Dashboard (our scopes +
  protected customer data name/address), install it on their store and paste its client ID + secret.
  We get a token with the **client credentials grant** (`ShopifyProvider.own_app_token`: 24 h, no
  refresh token — `refresh_access_token` simply asks again with the same credentials). Shopify only
  allows it when app and store are in the same organization (`shop_not_permitted` otherwise; the
  view translates Shopify's raw errors). The client ID lives in `preferences["own_app_client_id"]`
  (`OWN_APP_CLIENT_ID_PREF`, in `providers/base.py`) and the secret, encrypted, in `webhook_secret`,
  because **that app's webhooks are signed with its own secret**: the receiver tries our app's secret
  first and then `provider.verify_store_webhook(..., connection)`. Installing our app later
  (`connect_store`) drops both. The print menu extension belongs to our app, so it does not exist on
  this path. The scopes listed in `tiendas.html` must match `SHOPIFY_SCOPES`. **Tiendanube has no
  manual path**: its API only grants access to an installed app (authorization code only).
- **WooCommerce (`providers/woocommerce/provider.py`)** — every store is a self-hosted WordPress, so: credentials
  are REST **API keys** (consumer key + secret, stored encrypted as JSON in `access_token`, never expire)
  that arrive by **two paths**, decided with the user: the automatic `/wc-auth/v1/authorize` (the
  generic `woocommerce/install-url/?shop=` builds it with our signed `state` as `user_id`; WooCommerce
  POSTs the keys server-to-server to `woocommerce/keys/` — which answers without calling the store,
  since the store is blocked waiting on us — and sends the browser to `woocommerce/return/`), or keys
  pasted by hand at `woocommerce/connect-manual/`, which ARE tested against the store first. Both end in
  `stores.connect_with_credentials`. No `callback/` route (`uses_authorization_code = False`).
  **HTTPS only** (over HTTP the API demands OAuth 1.0a signing — not implemented); Basic auth, falling
  back once to keys in the query string for hosts that strip `Authorization` (remembered in
  `preferences["woo_auth"]`). Webhooks are **ours, signed with a per-store secret**
  (`StoreConnection.webhook_secret`, `webhook_secret_per_store`): delivered to
  `woocommerce/webhooks/?store=<id>`, so the generic receiver finds the store BEFORE verifying; the
  creation "ping" (`webhook_id=N`, no topic) gets a bare 200 (`parse_webhook` → `None`). WooCommerce
  disables a webhook after 5 failed deliveries and fires them from WP-Cron, so it has
  `supports_reconciliation`: each worker loop, `stores.enqueue_due_reconciliations` queues
  `internal/reconcile_orders` every `INTEGRATIONS_RECONCILE_MINUTES` (30) per store — re-enables the
  webhooks and re-syncs orders modified since the last pass (`modified_after`, 10 min overlap). No
  uninstall event (a 401 marks the store "error"). Dispatch = order → `completed` + a customer-visible
  note with carrier/number/URL (no tracking field without plugins; decided 2026-10-01), never
  duplicated. Province codes (`C`, `B`, `X`…) map to names for AR; `address_1` goes through
  `providers/addresses.split_street` (shared with Shopify); no shipping address → billing; buyer
  email/phone are never stored.   **The webhook secret is created once, reading the row under lock** (`ensure_webhook_secret`):
  the worker loads a whole batch's connections at once, and a stale copy without a secret used
  to mint a second one, so every webhook from that store got 401 forever.
- **Printing from the WooCommerce admin (`providers/woocommerce/admin_print.py` + our WordPress plugin).** The plugin
  "Rótulos de envío" lives in `backend/apps/integrations/providers/woocommerce/wordpress_plugin/rotulos-envio/` — inside
  `backend/` because the API image only copies that folder, and the backend serves it as a zip. It adds
  a "Print shipping labels" bulk action to WooCommerce → Orders (both the HPOS and the legacy posts
  screens). The PHP calls `POST woocommerce/print-link/` **server to server** with `{store, ids, ts}`
  signed in `X-Rotulos-Signature` (base64 HMAC-SHA256) with the store's `webhook_secret`, then
  `wp_safe_redirect`s the browser (new tab) to `GET woocommerce/print/<token>` (its host is whitelisted
  through `allowed_redirect_hosts`). Order resolution, the signed link and the PDF are
  the shared `store_print.py`'s (`read_print_token`/`print_url` take the platform/route, `order_ids` validates
  numeric ids for Woo and Tiendanube).
  - **The merchant configures nothing**: the backend writes the endpoint, store id and secret into the
    plugin through WooCommerce's own settings REST API (`POST settings/rotulos/batch`,
    `StoreProvider.configure_admin_print`) on setup, on every reconciliation and on demand
    (`POST stores/<id>/check-print-plugin/`, the "Verificar plugin" button). 404 = plugin not
    installed, which is fine. The result is kept in `preferences["print_plugin"]` (written with an
    UPDATE, never `save()`, because the worker holds stale copies) and exposed as
    `print_plugin_linked`. Each setting the plugin declares **needs `option_key`**, or WooCommerce
    answers 200 and stores nothing.
  - **Checkout quotes (`providers/woocommerce/rates.py`, plugin `includes/shipping.php`).** WooCommerce has
    no carrier API, so the plugin registers a shipping method ("Shipping labels (rates table)",
    id `rotulos`) the merchant adds to their zones. At checkout it POSTs `woocommerce/rates/`
    `{postcode, country, weight_kg, cart_total, currency}` signed like the print request (the cart weight is
    converted to kg in WordPress with `wc_get_weight`), and the backend answers from the same
    `ShippingRate` table and `matching_rates` rule as Tiendanube. Same rule as there: **it never
    breaks the sale** — only a bad signature is 401; any other failure answers `{"rates": []}`, and
    the plugin, on timeout (5 s) or error, offers nothing and logs to WooCommerce's logger. Answers
    are cached 5 min in a transient (WooCommerce recalculates on every checkout refresh); a rate in
    another currency than the store's is dropped; a non-AR destination is not quoted (the table's
    postal codes are Argentine). The rates URL is one more setting the backend writes
    (`rotulos_rates_url`). `tiendas.html` tells a linked store how to turn it on, and
    `tarifas_envio.html?store=<id>` opens with that store chosen.
  - **Getting it to the merchant**: `GET woocommerce/print-plugin/` returns the zip
    (`plugin_zip()`: everything under a top-level `rotulos-envio/` folder, which is WordPress' identity
    for the plugin — another name would install an update beside the old one). `tiendas.html` shows,
    on each active WooCommerce store, the download, a link to the store's own
    `wp-admin/plugin-install.php?tab=upload`, and "Verificar plugin".
  - **Prepared for the WordPress.org directory** (not submitted): English source strings with bundled
    `languages/` es_ES/es_AR (`.po` + `.mo`, rebuild with `msgfmt`), `readme.txt` with the mandatory
    "External services" section, `uninstall.php`, `Requires Plugins: woocommerce`, GPLv2+. Still
    missing for the submission: a WordPress.org account (`Contributors:` in readme.txt) and a public
    privacy-policy URL to cite. Once listed, the download can become a link to
    `plugin-install.php?tab=plugin-information&plugin=<slug>` on the merchant's store.
- **VTEX (`providers/vtex/`, its own folder on request: `provider.py` orders/hook/feed/dispatch,
  `freight.py` checkout quotes, `views.py` the connect view, `common.py`, `tests/`; frontend in
  `frontend/assets/js/vtex/`) — written 2026-10-08 WITHOUT an account**: everything comes from the
  official Orders API reference (its OpenAPI) and is tested against `FakeVtex` (`tests/fake_vtex.py`);
  points to check on the first real account are marked "A CONFIRMAR". Connection is only by hand:
  `POST vtex/connect-manual/` `{account, app_key, app_token}` (`VtexManualConnectView`, tested against
  VTEX first, answers `warnings` for missing non-essential permissions) → `stores.connect_with_api_credentials`
  (the generic half of `connect_with_credentials`). No `install-url/` (`uses_install_url = False`) nor
  `callback/`. `external_store_id` is the **account name** (`micuenta` of `micuenta.myvtex.com`), which
  also builds the API host (`VTEX_API_HOST_TEMPLATE`, `{account}.vtexcommercestable.com.br`); the
  credentials are JSON in `access_token`. The key needs a **custom role** (no predefined one has the
  hook): OMS "List Orders", "View order", "Feed v3 and Hook Admin", "Notify invoice", "Change order
  workflow status" — a 403 names the missing resource.
  - **Hook + feed.** The *hook* (one per appKey) POSTs `{OrderId, State, Origin.Account}` to
    `vtex/webhooks/?store=<id>` with the headers we chose: the per-store secret goes in header `key`
    (`webhook_secret_per_store`; the hook isn't signed). Configuring it makes VTEX ping
    (`{"hookConfig": "ping"}` → bare 200) and it refuses to save it if the ping fails; VTEX also deletes a
    hook after 3 days without notifications, so it is re-checked on every pass. **A hook pointing to
    another system (the merchant's ERP using the same key) is never overwritten**: `last_error` asks for a
    dedicated key. The backup is the *orders feed* (a queue VTEX keeps up to 14 days), read on every
    reconciliation (`VTEX_RECONCILE_MINUTES`, 5 — `StoreProvider.reconcile_minutes` lets each platform
    have its own interval); it needs no public URL, so without a hook orders still arrive. A page's feed
    items are committed on the NEXT page, after the handler stored their orders. The order list is only
    used for the initial import (statuses ready-for-handling/handling/invoiced, VTEX caps it at 30 pages);
    it can't filter by "modified since" and VTEX says not to use it for integrations.
  - **Dispatch = tracking on the invoice; we never invoice** (an Argentine invoice is fiscal, issued by
    the merchant's ERP; decided 2026-10-08). `push_fulfillment` moves `ready-for-handling` →
    `start-handling`, PATCHes `trackingNumber`/`trackingUrl`/`courier` onto each Output invoice, and for
    delivered PUTs a tracking event with `isDelivered`. No invoice yet → `ProviderError` (retried); when
    the invoice shows up later (hook/feed) `handlers._store_order` asks
    `StoreProvider.needs_fulfillment_push` and enqueues the push again. Local status from VTEX: canceled
    → cancelled, invoiced WITH tracking → dispatched (invoiced alone isn't: ERPs invoice before shipping),
    `courierStatus.finished` → delivered. `external_number` is the `orderId` (what the admin shows).
    `clientProfileData`/payments/contact info are never stored (`_stored_copy`).
  - **Checkout quotes = a published freight table, not a callback** (added 2026-10-08). VTEX's checkout
    never asks an outside party for a price: it quotes from the freight tables of its shipping
    policies. So `push_shipping_rates` publishes the `ShippingRate` table as one shipping policy per
    `option_code` (`rotulos-<code>`, created if missing, only renamed afterwards — never re-activated)
    with its freight rows (Logistics API, permission "Logistics shipping full access"). `freight_rows`
    rebuilds `matching_rates`' rule ("tightest bracket that fits") as non-overlapping rows: postal
    ranges are split into segments with the same rates, brackets become consecutive weight ranges
    (grams, `VTEX_FREIGHT_WEIGHT_UNITS_PER_KG`), equal neighbours merge back. VTEX's update endpoint
    APPENDS rows, so the last published rows are kept in `preferences["vtex_freight"]` and only the
    difference is sent (deletes with `operationType` 3, then inserts). A row needs a delivery time:
    rates without days go out with `VTEX_DEFAULT_DELIVERY_DAYS` (5). A policy only quotes once the
    merchant links it to a dock in their admin — we don't touch their docks; the unlinked ones are
    reported. Publishing turns the option on in THEIR checkout, so it is the merchant's call:
    `POST stores/<id>/publish-rates/ {enabled}` (generic: `StoreProvider.supports_rates_push`;
    refuses with no active rates) sets `preferences["rates_push"]` and enqueues
    `internal/push_shipping_rates`; after that every create/update/delete in `ShippingRateViewSet`
    calls `shipping_rates.rates_changed`, which re-enqueues it (one pending event absorbs a burst).
    State (`pending`/`published`/`failed`, rows, unlinked policies, error) is exposed as `rates_push`
    on the store serializer and shown in `tarifas_envio.html` ("Publicar en el checkout de VTEX", JS in
    `assets/js/vtex/tarifas_vtex.js`). A
    missing logistics permission fails only the publication, never marks the store "error".
  - Not done (phase 2): printing from the VTEX admin (needs a VTEX IO app).
- **Magento 2 / Adobe Commerce (`providers/magento/`, its own folder: `provider.py`, `oauth.py` the
  request signing, `views.py` the connect view, `tests/`; frontend in `frontend/assets/js/magento/`) —
  phase 1, written 2026-10-08 WITHOUT a store**
  (the laptop has 3.5 GB of RAM: a local Magento + MySQL + OpenSearch doesn't fit; the real test is
  meant for a client's store or a rented server). Tested against `FakeMagento` (`tests/fake_magento.py`),
  which re-derives the OAuth signature from the URL that actually arrived; "A CONFIRMAR" marks what
  needs a real store. Connection only by hand: the merchant creates an *Integration* (System →
  Extensions → Integrations, custom resources: Sales → Operations → Orders with View/Ship/Comment, and
  Shipments), activates it and pastes its four credentials into `POST magento/connect-manual/`
  (`MagentoManualConnectView`, tested against the store first) → `connect_with_api_credentials`.
  `external_store_id`/`store_url` work like WooCommerce's (site URL, **HTTPS only**). **Every request is
  signed with OAuth 1.0a HMAC-SHA256** (`oauth1_header`, stdlib only) instead of sending the token as
  Bearer: since 2.4.4 that is off by default and turning it on is a setting Adobe advises against. The
  query string is built by us with the same encoding that was signed (bracketed `searchCriteria[...]`
  keys would otherwise break the signature). A host without URL rewrites serves the API under
  `/index.php/rest/`: tried on connect and kept in `preferences["magento_rest_path"]`.
  - **No webhooks in Magento Open Source** → `order_sync_events = ()`, `register_webhooks` is a no-op
    (and `handlers._register_webhooks` no longer demands a public URL for a platform without webhook
    events). Orders arrive by reconciliation every `MAGENTO_RECONCILE_MINUTES` (5), which is exact here:
    the API filters by `updated_at` (`YYYY-MM-DD HH:MM:SS`, UTC). The order list already carries full
    orders; past the last page Magento repeats the last one instead of returning empty, so paging
    stops on `total_count`. Shipping address comes from `extension_attributes.shipping_assignments`
    (billing as fallback); a configurable product's child line (`parent_item_id`) is skipped;
    `external_number` = `increment_id`, `external_id` = `entity_id`. State canceled/closed →
    cancelled, complete → dispatched.
  - **Dispatch creates a shipment** (`POST order/{id}/ship` with a `custom` carrier track, title =
    `Order.carrier`, Magento emails the buyer); if the merchant already shipped from their admin, only
    the missing track is added (`shipment/track`) and the shipment email resent. Magento has no field
    for a custom carrier's tracking URL: it goes in a buyer-visible order comment, never repeated.
    **We never invoice** (in Magento invoicing captures the payment). "Delivered" doesn't exist there.
  - Phase 2 (not done): our own Magento module for instant order notifications, a "print labels" mass
    action and checkout quotes (a Magento carrier calling our rates) — like the WooCommerce plugin.
- **Empretienda (`providers/empretienda/`) — no API at all** (none public or for partners as of
  2026-10; DUX, which integrates it, also imports by hand). Its only output is the spreadsheet from
  "Gestión de ventas → Listado de ventas → Exportar". So `EmpretiendaProvider` talks to nobody: it
  exists so the store is a normal `StoreConnection` (orders grouped, sender/logo/template, store filter,
  idempotent by order number) — no credentials, webhooks, auto import (`supports_order_import = False`)
  nor dispatch push (tracking is entered in Empretienda's admin). `POST empretienda/connect/`
  `{store_url, name}` adds it (`external_store_id` = the store's domain, so two accounts can't load the
  same one). Orders come in through `importar_empretienda.html` (JS in `assets/js/empretienda/`):
  `POST empretienda/import/preview/` then `confirm/`, multipart `{store, file, mapping?}` — the file is
  sent in both steps, so a spreadsheet with buyers' data is never stored between them.
  `spreadsheet.py`: **the real column names aren't published anywhere** — `FIELDS` holds the most
  likely synonyms (A CONFIRMAR with a real export) and the merchant fixes any column in the preview; the
  fixed mapping is kept in `preferences["empretienda_mapping"]` and used first next time. Detection
  reuses `apps.orders.bulk.match_columns` (generalized from the tracking import), with one-word
  synonyms only matching exactly (so "numero" doesn't take "numero de telefono"). Rows of the same
  order (one per product) are grouped; each order goes through `upsert_store_order`, so re-importing
  overlapping ranges updates instead of duplicating, and status only moves forward. Status from the
  status columns (A CONFIRMAR: exact texts): cancel/anulad → cancelled, entregad → delivered, en
  camino/en tránsito → in_transit, enviad/despachad → dispatched. Email, phone and DNI columns are never
  read; `raw_payload` keeps only the order number and the status texts. Permission: `orders.create`
  (it's how THEIR store's orders arrive, like the other platforms do automatically), not the generic
  `orders.import`, which merchants don't have.
- **Local test store**: WordPress + WooCommerce in podman (`woo-wp` on 127.0.0.1:8080, `woo-db`),
  published by Tailscale Funnel at `https://<laptop>.ts.net:8443`; `podman start woo-db woo-wp`
  after a reboot. No wp-cli in the image: run PHP through `podman exec` + `wp-load.php`.
- Install: logged-in merchant calls `GET <platform>/install-url/` (`?shop=` for Shopify); the redirect URL
  configured in the platform's panel is `GET <platform>/callback/` (public). Installed from the app store
  (no session), the store lands with
  `owner=None` and the callback redirects with a short-lived `store_claim` token, exchanged via `POST
  stores/claim/`;
  **Install link to share (Tiendanube only — platforms with `uses_authorization_code` and no
  `requires_shop_domain`)**: the merchant often isn't who administers the store. `POST
  tiendanube/install-share-link/` (`orders.create`) returns a public URL `.../tiendanube/install/<token>/`
  signed with the user (`make_share_token`, `INTEGRATIONS_INSTALL_SHARE_MAX_AGE_SECONDS`, 72 h); whoever
  opens it, with no account of ours, gets a FRESH short-lived `state` for that user marked `x=1`
  (`make_oauth_state(..., shared=True)`), and the callback (`oauth_state_is_shared`) sends them to the
  public `tienda_conectada.html` (`STORE_SHARED_CONNECT_FRONTEND_PATH`) instead of `tiendas.html`. Built
  from `INTEGRATIONS_PUBLIC_BASE_URL` (503 without it). The button lives in `tiendas.html`'s Tiendanube row. already-owned stores redirect with `store_connected=<id>`, failures with
  `store_error=<code>`. `POST stores/<id>/disconnect/` revokes (never deletes). `PATCH stores/<id>/settings/` saves how that store's
  labels print — `sender_name`/`sender_address`/`sender_phone`, `logo` (file or the editor's base64 data
  URL, 2 MB cap), `default_template` (must be public or the caller's) and `label_printer_dpmm` (its
  thermal printer's density, a column rather than a `preferences` key because the merchant edits it and
  it has to be validated; `null` = not configured) — never token/status; audited as `store.update` (the
  logo logs its filename, not the bytes). Edited per store in `tiendas.html`.
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
- `POST <platform>/webhooks/` (public) verifies the signature, reads it with `parse_webhook` and
  enqueues; `internal/*` types are handler-only, never real webhooks. Order events (the provider's
  `order_sync_events`) fetch the full order and `upsert_store_order` it — all orders, not only paid ones.
  The uninstall event revokes the store. `internal/store_setup` registers the provider's
  `webhook_events` then, if `supports_order_import`, enqueues `internal/import_orders`, which pages
  through the store's recent orders.
- Status sync from the store is forward-only (see `_synced_status` above). Push-back:
  `Order.save()` → `fulfillment.notify_store_shipping_change` (only when the provider's
  `fulfillment_status_for` maps that status) → enqueues `internal/push_fulfillment` →
  `TiendanubeProvider.push_fulfillment` PATCHes each fulfillment order's status (forward-only) and
  `tracking_info` only when the tracking code changed.
**Printing from Tiendanube's sales list (bulk-action app link).** Unlike Shopify/WooCommerce there is no
extension or plugin: Tiendanube lets an app register a *link* (Partner Portal → the app → Links → "Acciones
masivas") that shows up in Ventas' bulk-action menu and opens a URL with the selected orders. It points at
`imprimir_tiendanube.html` (+ `assets/js/tiendanube/imprimir_tiendanube.js`; the HTML stays at the root because its URL is registered in the Partner Portal). **Tiendanube doesn't sign that link**, so
the identity comes from our own session: the page calls `POST tiendanube/print-link/` (`labels.batch`, JWT)
with `{store, ids}`, the store must be the caller's active Tiendanube (`store` = its Tiendanube id; omitted →
their only one), and the answer is the same short-lived signed PDF link as Shopify/WooCommerce
(`store_print.resolve_orders`/`make_print_token`, route `tiendanube-print`). Without a session the page
stashes the query in `localStorage.pendingTiendanubePrint` and `index.html` returns to it after login. Tiendanube
opens it as `?locale=es&store=<tiendanube store id>&id[]=<order id>&id[]=...` (seen live 2026-10-07; the
ids are Tiendanube order ids = our `external_id`, not order numbers). The format isn't publicly documented,
so the page also accepts `ids`/`orders`/... (comma lists too) and, when it finds none, prints what it
received. Configured in the Partner Portal as category "Órdenes", "listado de órdenes". Working end to end
(printed live from demosbuspack on 2026-10-07).

**Labels the store asks for (`providers/tiendanube/labels.py`, `label_views.py`, `label_urls.py`, `handlers.py`).** The mirror image of
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
**Quoting shipping at checkout (the shared table and rule in `shipping_rates.py`; Tiendanube's callback in `providers/tiendanube/rates.py`, `rate_views.py`, `rate_urls.py`).** The other half of
being a carrier: `labels` resolves the label *after* the sale, this resolves the price *before* it.
`POST .../tiendanube/rates/<token>` receives the cart and answers `{"rates": [...]}` — the options the buyer
sees at checkout. Same signed per-store token as the labels callbacks (the cart's `store_id` is body data,
not proof). It touches nothing but the DB: no API calls, no queue, because **it sits in the middle of
someone else's sale**. Tiendanube runs a circuit breaker (500 requests / 30 min window, 50% failure rate →
5 minutes of no traffic), and while it's open our shipping option simply vanishes from the checkout. That
is why the view **never returns 5xx**: an unreadable cart or an unexpected exception answers `{"rates": []}`
(logged), which means "I don't ship there" and leaves the buyer's other options intact.
- **Prices come from a per-store table** (`ShippingRate`), decided 2026-09-23: destination postal code ×
  weight bracket → price. `postal_code_from`/`_to` cover a zone or a single code (equal values), normalized
  to the 4 comparable digits (`normalize_postal_code`, so a CPA `C1602ABC` matches `1602`).
  `weight_up_to_kg` is the bracket ceiling (`null` = no ceiling); per `option_code`, the tightest bracket
  that fits the cart wins, and if none fits that option isn't offered. **A postal code outside the table is
  not quoted** — an empty list, never an invented price. Per store and not global: each client ships from
  their own origin with their own negotiated rates.
- **The merchant's table.** `/api/v1/integrations/shipping-rates/` (`orders.create`, full CRUD, `?store=`)
  with `tarifas_envio.html` + `assets/js/tarifas_envio.js`, linked from `tiendas.html` and the
  `shipping_rates` menu item. Audited as `store.update`.
- **Carrier registration is manual, on purpose.** `python manage.py register_store_carrier [<store id>]`
  calls `tiendanube.labels.register_carrier`, which registers both callbacks (`callback_url` =
  `tiendanube.rates.rates_callback_url`, `callback_labels_url` = `labels.callback_base_url`) and
  **refuses if the store has no active rates** — a carrier with no table would offer a shipping method that
  never answers a price. It is manual because registering flips that store's checkout on: it is turned on
  client by client, never as a side effect of installing the app.
- **Carrier options (`providers/tiendanube/carrier_options.py`) — without them nothing shows.** Tiendanube
  docs: "post all your available rates, our API will filter by carrier options active" — a rate whose
  `code` has no ACTIVE option of our carrier (`/shipping_carriers/{id}/options`) is dropped silently. So
  `register_carrier`, after registering, syncs one option per code the callback can answer: each distinct
  `option_code` of the store's active `ShippingRate`s (name = its first row's `option_name`) plus each
  enabled checkout carrier (`apps.carriers.checkout.CARRIERS`: `"andreani"` with
  `preferences["andreani_checkout"]["name"]`; a table row with the same code wins, as in
  `with_carrier_rates`). Idempotent (`TiendanubeProvider.sync_shipping_carrier_options`): an existing
  code is only renamed; `active`/`additional_days`/`additional_cost`/`allow_free_shipping` are never sent
  (they're the merchant's, set in their panel — an option they switched off is NOT re-activated, it's
  reported as `inactive`), and options for codes we no longer quote are never deleted (no rate = not
  shown; deleting would lose their settings). Result in `preferences["shipping_carrier_options"]`
  (`status` synced/failed, `codes`, `created`, `renamed`, `inactive`, `error`); the command prints it.
  If the sync fails the carrier stays registered and `internal/sync_carrier_options` is enqueued.
  **Re-sync**: `shipping_rates.rates_changed` (every `ShippingRateViewSet` write) and the Andreani
  checkout `PUT` (only when `enabled` or `name` change) call `StoreProvider.checkout_prices_changed`
  (no-op by default); Tiendanube's enqueues that event if the store has a carrier id — the worker
  (`tiendanube/handlers.sync_carrier_options`) does the API calls, never the merchant's request nor the
  checkout callback. 401/403/404/422 (e.g. the carrier was deleted from the panel) fail the event without
  retries. Diagnostic: the cart Tiendanube sends lists `carrier.options`; `rates.quote` logs a WARNING
  when it answers a code missing there.
- **Plan gating.** `connect_store` saves the store's `features` into `StoreConnection.preferences`, and
  `tiendanube.labels.supports_label_api()` reads `fulfillment_order_label_api` off it (`None` = unknown, for
  stores connected before this existed). Surfaced as `label_api_enabled` on the store serializer.
- **The merchant's view.** `GET /api/v1/integrations/store-labels/` (`orders.create`, read-only, filters
  `?store=` and `?status=`) lists the caller's own store label requests with the failure reason — never
  the stored `payload` (buyer data) nor the download token. `rotulos_tienda.html` +
  `assets/js/rotulos_tienda.js` render it, linked from `tiendas.html` and the `store_labels` menu item.

- Privacy webhooks (`privacy.py`, work for revoked stores too): `customers/redact` anonymizes the matched
  orders, `store/redact` revokes the store and anonymizes everything, `customers/data_request` emails a
  JSON report to the store owner (no owner → fails without retry, report stays in `event.result`). Orders
  are anonymized, never deleted.

### Carriers (`apps/carriers`) — Andreani

The opposite direction from `apps.integrations`: there a store sends us orders; here we ask a **shipping
company** to carry them. **Each client uses their OWN account and contracts** (decided 2026-10-08; never
an account of ours as default), so everything hangs from `CarrierAccount.owner`. Written 2026-10-08
**without credentials** (Andreani only gives QA credentials to clients, through their sales rep): it
comes from Andreani's official docs, published as spreadsheets at developers.andreani.com/document, and
is tested against `FakeAndreani` (`andreani/tests/fake_andreani.py`); "A CONFIRMAR" marks what needs a
real account. Their Warehouse service (stock, order preparation) is another API, left for later.
- Models (`apps/carriers/models.py`, shared by every carrier): `CarrierAccount` (one per owner and
  carrier: credentials encrypted with `apps.integrations.crypto`, `client_code`, `contracts` as
  `[{code, label, kind: "home"|"branch"}]` — one contract per service —, sender + origin address, default
  package weight/volume, cached 24 h token), `CarrierShipment` (one per order and carrier shipment:
  `tracking_number` = Andreani's *número de envío*, `group_number` = *agrupadorDeBultos*, `status`
  pending/in_transit/at_branch/delivered/issue/returning/cancelled, Andreani's own text in
  `carrier_status`) and `CarrierShipmentEvent` (the traces, unique per shipment+moment+event).
- **Andreani lives in `apps/carriers/andreani/`** (`client.py` the API, `shipments.py` order ↔ shipment
  and status mapping, `views.py`/`urls.py`, `tests/`). API: `GET /login` with Basic auth → token in header
  `x-authorization-token` (A CONFIRMAR whether it can come in the body; both are read), renewed at 23 h or
  on the first 401; QA and production chosen per account. Order v2 (`POST /v2/ordenes-de-envio`:
  `contrato`, `origen.postal`, `destino.postal` or `destino.sucursal.id`, `remitente`, `destinatario` is a
  LIST, `bultos` with `kilos`/`volumenCm` and `referencias: [{meta: "idCliente"}]`), labels
  (`GET .../{agrupador}/etiquetas`, PDF or `Accept: application/zpl`), tracking v3
  (`GET /v3/envios/{n}/trazas`), branches (`GET /v2/sucursales?codigoPostal=`, no token) and cancellation
  (`POST /v2/nueva-accion`, `accion: cancelacion`).
- **Creating a shipment** (`POST carriers/andreani/shipments/` `{order_ids, contract, branches?,
  package_count?}`, `orders.create`, one failing order never blocks the rest) stores the shipment and,
  through `orders.shipping.apply_shipping`, sets `Order.carrier = "Andreani"`, `tracking_number` and
  `tracking_url` (`ANDREANI_TRACKING_URL_TEMPLATE`, A CONFIRMAR) and moves the order only to **preparing**:
  created is not dispatched. **The label's barcode is the Andreani number with no extra code**: the
  rótulo prints `{{tracking}}` = `Order.tracking_number` (that's the "number under the barcode" the user
  asked for). One open shipment per order; cancelling clears the order's tracking so it can be sent again.
- **Quoting** (`shipments.quote_order`, `POST carriers/andreani/shipments/quote/` `{order_ids, contract,
  package_count?}`): `GET /v1/tarifas` (official sheet `api-cotizador-v2-1.xlsx`) with `cpDestino` = the
  order's postal code (also for branch contracts: the rate is by destination zone), `contrato`,
  `cliente` = `CarrierAccount.client_code` (required to quote, not to ship), and one `bultos[i][kilos|volumen]`
  per package (the same weight split as the order). Answers per order `price` (with VAT),
  `price_without_tax`, `insurance`, `chargeable_weight_kg` and the `total`; creates nothing.
  The "Despachar" dialog in `pedidos.html` quotes on opening and shows each order's price and the total
  before "Crear envíos" (re-quoted when the contract or packages change). On create the quote is repeated and stored as
  `CarrierShipment.quoted_price` — **best-effort**: a failed quote never blocks the shipment
  (`quoted_price` null). A CONFIRMAR: whether `/v1/tarifas` needs the token (it's sent anyway).
- **Andreani's price in the store checkout (`andreani/checkout.py`).** On platforms whose checkout asks
  us live (`StoreProvider.quotes_at_checkout`: Tiendanube's carrier callback, WooCommerce's plugin) a
  store can add an "Andreani" option quoted with the STORE OWNER's account. Turned on per store in
  `preferences["andreani_checkout"]` (`enabled`, `contract` — home contracts only, a branch would need
  the buyer to pick it —, `name`, `delivery_days_min/max`) via `GET/PUT carriers/andreani/checkout/`
  (+ `checkout/test/`, uncached) and `checkout_andreani.html`. Both callbacks still call
  `matching_rates` and then `shipping_rates.with_carrier_rates`, which appends
  `apps.carriers.checkout.carrier_rates` as unsaved `ShippingRate`s (a table rate with the same
  `option_code` wins). Because it sits in someone else's sale: `ANDREANI_CHECKOUT_TIMEOUT_SECONDS` (4),
  answers cached by account+contract+CP+weight rounded UP to 0.5 kg (`ANDREANI_CHECKOUT_CACHE_SECONDS`,
  30 min; Django's default cache, per process), failures cached too
  (`ANDREANI_CHECKOUT_FAILURE_CACHE_SECONDS`, 2 min) so a down Andreani doesn't cost 4 s on every
  checkout; any failure just drops the option. A cart without weights quotes the account's default
  package. **What the buyer pays** (`buyer_price`) = Andreani's total with VAT × (1 + `surcharge_percent`)
  + `surcharge_amount`, or 0 when the cart reaches `free_shipping_from` (the merchant pays). The real
  cost travels as `merchant_price` on the unsaved rate, sent to Tiendanube as `price_merchant` (table
  rates keep price_merchant = price). The cart total comes from Tiendanube's `total_price` (else the
  items' `price` × `quantity`) and from the WooCommerce plugin's `cart_total` (plugin 1.2.0+); without it
  there is no free shipping. The cache stores the cost, not the final price. Tiendanube's own panel
  additional cost / free shipping per carrier option apply on top of ours. `register_carrier` now accepts a store with no table if a carrier
  quote is enabled (`has_checkout_prices`), and creates the `andreani` carrier option (see "Carrier options"). VTEX isn't covered (it quotes from published tables).
- **HOP points are just Andreani branches** (nomenclature `HOPxxxx`, "PUNTO ANDREANI HOP …"): a branch
  contract sends to `destino.sucursal.id`, and `branches/?cp=` marks them `is_hop`.
- **Tracking** (`shipments.sync_shipment`): the worker calls `apps.carriers.tracking.sync_due_shipments`
  each loop (open shipments not checked for `ANDREANI_TRACKING_POLL_MINUTES`, up to
  `ANDREANI_TRACKING_MAX_DAYS`). Events map by Andreani's "Maestro de eventos y estados": **the cycle
  matters** — `EnvioEntregado` in a return cycle (Drop/Devolucion/Rescate) is the parcel going back to the
  sender, not delivered. Order status moves forward only: admitted (`Admision`/`AltaAutomatica`) →
  dispatched (that's when the store gets the tracking), moving or waiting at a branch/HOP → in transit,
  delivered to the buyer → delivered; returns and issues stay visible on the shipment only. A rejected
  login deactivates the account (`last_error`, shown in `andreani.html`) until the client saves it again;
  any other failure is noted and the shipment is retried on the next interval, not every loop.
- Frontend — **everything Andreani lives in "Mis pedidos", no menu entries of its own** (decided
  2026-10-09: separate "Envíos/Cuenta/Checkout Andreani" buttons made the user pick the same orders in
  another page and still type the tracking by hand in `despachar.html`). In `pedidos.html`:
  `assets/js/pedidos/envios.js` (`window.OrderShipping`) draws, per order, the carrier + tracking link +
  Andreani's status (from `GET shipments/`, latest non-cancelled per order), the buttons "Despachar" /
  "Imprimir" / "Actualizar seguimiento" / "Cancelar envío", the checkbox + sticky selection bar
  ("Despachar (n)", "Imprimir (n)") and a strip with the account state linking to `andreani.html`
  and `checkout_andreani.html` (those two pages remain, reached only from there, topbar back to
  `pedidos.html`). Both dialogs refresh the list from their own close() (and from the `close` event, for
  Esc) — the event alone didn't fire in a hidden browser pane. `assets/js/pedidos/despacho.js`
  (`window.OrderDispatch`) is the `<dialog>`: account
  not ready → "Conectar Andreani"; ready → service (contract, home first; hidden if only one), packages,
  per-order branch/HOP for branch contracts, automatic quote, "Crear envíos" → result with the tracking
  numbers and "Imprimir rótulos y etiquetas".
- **Printing = OUR rótulo + Andreani's label** (decided 2026-10-09, "los dos"): `POST shipments/print/
  {shipment_ids}` (`andreani/printing.py`) returns ONE PDF with, per shipment in the requested order, the
  rótulo drawn with the store's template (`store_print.resolve_template`, else the default public one;
  `{{tracking}}` is already Andreani's number) followed by Andreani's label pages, joined with `pypdf`.
  No template / a broken design → only Andreani's label; an unreadable Andreani PDF → 502, never half a
  batch. `GET shipments/<id>/label/` and `POST shipments/labels/` (Andreani's alone, zip/ZPL) remain.
  A CONFIRMAR with Andreani whether one of the two is enough. For one order it also links "Otro transportista: cargar el seguimiento a
  mano" (`despachar.html`, kept for carriers without API). `envios_andreani.html` is no longer linked
  from anywhere. JS for the account/checkout pages in `assets/js/andreani/`.
- **Trying it without credentials**: `python manage.py fake_andreani_server` (`apps/carriers/management/`)
  serves `FakeAndreani` on `http://127.0.0.1:8099` (real PDF labels saying "PRUEBA"; every tracking query
  advances one step: admitted → in transit → delivered; shipment numbers start from the clock so a
  restart never repeats one already in the DB). Set `ANDREANI_API_BASE_QA=http://127.0.0.1:8099`
  in `.env`, restart the backend, and save the account in QA with `cliente-prueba` /
  `Clave-Andreani-1` and contract `400006709` (home) or `400006710` (branch). Local only.
- Not done yet: the push "novedades" (Andreani configures them by hand per client), and
  Warehouse (stock management is not part of the product for now).

### Frontend

Plain multi-page app, one HTML file per screen, no framework/bundler. Admin pages
(`gestionuser.html`/`roles.html`/`reportes.html`/`auditoria.html`/`pedidos_admin.html`/`soporte_admin.html`)
share the same script order — `config.js`, `auth.js`, `utils.js`, `admin_common.js`, `admin_sidebar.js`,
then the page's own script — and each redirects to `dashboard.html` if the caller lacks that page's
permission (UI-only gating; the backend re-checks every permission server-side).

- `assets/js/utils.js` — shared helpers loaded as a classic script (global functions, not an ES module):
  `escapeHtml`, `getErrorMessage`, `showMessage`, `formatDate`, `extractResults`. `showMessage`
  scrolls the page to `#pageMessage` when it is out of view: the notice sits at the top, and an
  action taken further down used to end with no visible result at all.
- **Every action says how it ended.** A write that succeeds shows a green `page-message success`
  (or the section's own `.store-sender-msg`) and a failure a red one; nothing ends in silence or
  only in a redirect. Store connections report in `#connectMessage` inside the connect card
  (`showConnectResult`), with the HTTP status (or the platform's error code) as "Error NNN:" so the
  merchant can quote it. `tiendas.js` (+ each platform's `assets/js/<platform>/tiendas_<platform>.js`) re-renders every card on `loadStores`, so a notice for one store
  goes into its NEW card (`showStoreFeedback`, cards carry `data-store-id`). In `tiendas.html` the
  "Conectar una tienda" card is a `<details>`: closed when the user already has stores (so "Mis
  tiendas" is reachable without scrolling), open with none, and `showConnectResult` opens it. Each
  store's "Rótulos de esta tienda" form is a `<details>` too, closed by default; the ones the user
  opened are kept in `openSenderForms` so a re-render (e.g. after saving) doesn't close them. The
  "Conexión mediante API" button next to the title links to `integraciones.html` and is shown only
  with `integrations.manage` (that page is admin-only). It is the only way in: `integraciones.html`
  is deliberately not in the dashboard menu (`DashboardView`). Batch printing lands on
  `documentos.html?labels=N[&dispatched=M]`, which confirms it and cleans the URL.
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
- `pedidos.html` is the work screen: the order list comes first (dispatch, see Carriers → Frontend),
  then "Mis direcciones" and "Nuevo pedido". It paginates its order history (`?page=`, Anterior/Siguiente driven by the API's `next`).
  Its store filter is one button per store (plus "Todas" and "Cargados a mano"), built from
  `GET /integrations/stores/` — never a hardcoded list — and rebuilt when the tab becomes visible again,
  so a store connected in `tiendas.html` shows up by itself. Each store gets a color (`store-color-N` in
  `pedidos.css`, assigned by store id order so it doesn't shift when another store is added) that also
  marks its orders' tag and left border.
- `assets/js/admin_common.js` — permission/formatting helpers for admin pages: `isAdminMode()`,
  `canUseUserPermission()`, `canViewUsers()`, `translateRole()`, `actionLabel()`, `formatDateTime()`,
  `renderSimplePager()`, `loadAuditActionsCatalog()`.
- `assets/js/auth.js` — `window.Auth` (`apiFetch`, `getAccessToken`, `getCurrentUser`, `logout`).
  `apiFetch` attaches the Bearer token, retries once through a single shared refresh on 401, and on
  failure clears the session and redirects to `index.html` (throwing an error with `.isSessionExpired`).
- **"Olvidé mi contraseña" (two pages, both public).** `recuperar-password.html` +
  `assets/js/recuperar_password.js` asks for the email (`POST /auth/password-reset/`);
  `reset-password.html` + `assets/js/reset_password.js` takes `uid`/`token` off the query string — the
  link in the email — and posts the new one (`POST /auth/password-reset/confirm/`). Neither loads
  `auth.js`: there is no session to attach, which is the whole point. `PASSWORD_RESET_URL` in `.env` is
  what the email links to, and it must name the real file (`.../reset-password.html`) — its default
  (`{FRONTEND_URL}/reset-password`) matches nothing in `frontend/` and the link 404s. The login's
  "Olvidaste tu clave?" used to be `href="#!"`: the screen was promised and did not exist. Reused
  `index_test.html` still has the dead link and is left alone (PHP-era leftover).
- **`roles.html` renders the whole permission catalog, and holds no list of its own.** It builds the
  sections from `RolePermission.category` and labels each checkbox with `RolePermission.name`, both of
  which the seed migrations already populate in Spanish. It used to carry a hardcoded whitelist of eight
  keys and *filter the API catalog against it*, so the backend enforced 41 permissions while an admin
  could see and assign 8 — the other 33 applied but were unreachable from any screen. Two regrouping
  rules by key prefix survive (`users.me.` → "Perfil propio", `plantillas.`/`variables.` → their own
  section) because those read as a different thing to whoever assigns them; they key off the data, not a
  list, so a new permission still needs no change here. An unknown category renders under its raw name
  rather than disappearing, and each checkbox carries its key in `title=` since two permissions can read
  alike.
- `assets/js/labels_api.js` — the only CRUD client for `apps.labels` (System 1): a real ES module,
  dynamically `import()`-ed from `saved_labels.js`/`templates.js` and used directly by `editor_rotulos.html`.
- `imprimir_rotulos.html` + `assets/js/print_labels.js` — **the** batch printing screen, and the only one:
  filter the caller's orders by store, status and creation-date range, tick them (the selection survives
  paging and filter changes), pick a template (default: each store's own) and layout, then
  `POST /api/v1/labels/batch/` with `order_ids`
  and land on `documentos.html` to download. `skip_existing` is on by default; an optional
  "mark as dispatched" runs `POST /orders/<id>/ship/` per order afterwards, and orders that can't be
  shipped are reported without breaking the rest. Linked from `pedidos.html`, from `mis_rotulos.html` and
  from the `labels_print` menu item (`labels.batch`).
  `mis_rotulos.html` used to carry a second, collapsible batch panel of its own (`filters` selector: date
  range + status). It was removed so each page has one function: its date-range capability moved here as
  the `?date_from=`/`?date_to=` list filter, which is strictly better because the store filter keeps
  applying — the backend's `filters` selector has no notion of store. `mis_rotulos.html` is now only the
  catalogue of saved `Label` rows (list, preview, edit, duplicate, delete).
- Orders that come from a store carry third-party text (buyer name/address, store name): any value
  inserted with `innerHTML`/`insertAdjacentHTML` must go through `escapeHtml` or use `textContent` instead.
- `google.js`/`google_test.js`/`assets/apis/access.php`/`index_test.html` are unused leftovers from a PHP-era
  Google-login prototype — not part of the real flow (`index.html` calls `GoogleAuthView` directly). Don't
  delete them. Their dependencies (`assets/apis/vendor/`, ~500 MB) are gitignored and regenerate with
  `composer install` from `assets/apis/composer.json`.
- `importar_rotulo.html` + `assets/js/importar_rotulo.js` drive the three-step photo import
  (`apps.processing`): upload the photo, read it into a proposal, review it (scaled preview drawn from the
  proposal's mm coordinates, detected values, confidence, discarded elements) and only then save it as an
  `ElementLayout`. The `_revision` key is stripped before POSTing — it exists for the reviewer, not for the
  serializer. Gated by `processing.import`; the menu entry used to read "Generar rótulo" (disabled), which
  named something else entirely — generating labels is what `mis_rotulos.html` does.
- `editor_layout.html` + `assets/js/editor_layout.js` is System 2's editor (`plantillas.edit`): drag
  elements on a canvas, edit the selected one's type/variable/content/mm box, add and delete elements,
  save with `PATCH /element-layouts/<id>/`. **Its preview is the server's own render**
  (`POST /element-layouts/<id>/render/` with `format: "png"`), shown as an image next to the canvas, and
  the `X-Layout-Missing`/`X-Layout-Truncated` headers surface as warnings. That is deliberate: the canvas
  places things (one mm→px factor for everything), the server says what comes out — so this screen cannot
  drift from the print output the way `editor_rotulos.html` does (see below).
- **Bulk order actions, one page each** (backend in `apps/orders/bulk_views.py`):
  `cargar_seguimientos.html` + `tracking_import.js` (no longer linked: **since 2026-10-09 it's the
  "Cargar seguimientos desde planilla" button of Mis pedidos** → `assets/js/pedidos/seguimientos.js`, a wide
  `<dialog>`: picking/dropping the file already previews it; the column mapping only opens when
  `order`/`tracking_number` weren't detected (else behind "Cambiar las columnas"); the store question only
  appears when there are `ambiguous` rows and 2+ stores; "Transportista" only when no carrier column is
  mapped, applied client-side to rows without one on confirm. Button shown with `orders.create`, taken
  from the Andreani account check. No menu entry),
  `planilla_retiro.html` + `dispatch_manifest.js` (no longer linked: **the manifest is built from Mis
  pedidos** since 2026-10-09 — selection bar "Planilla de retiro (n)" → `assets/js/pedidos/planilla.js`,
  a `<dialog>` whose carrier is prefilled when all ticked orders share one, and "Marcarlos como
  despachados" ticked by default, applied only to the ticked `created`/`preparing` ones via
  `bulk-status`; no menu entry),
  `estado_pedidos.html` + `bulk_status.js` (asks before cancelling) and `exportar_pedidos.html` +
  `export_orders.js` (by filter, no ticking). The two that work on ticked orders draw their filters and
  list with `createOrderPicker(root)` from `utils.js` (selection survives paging/filters); results go
  through `renderBulkResult` and files through `downloadResponse`, also in `utils.js`. They load
  `pedidos.css` + `imprimir_rotulos.css` + `acciones_masivas.css`. `print_labels.js` keeps its own copy
  of the list (it predates the picker and wasn't reorganized).
- **Addresses are not a menu entry.** They live inside `pedidos.html` (its own section, full CRUD); a
  separate `addresses` entry pointing at `pedidos.html#addressesSection` was a second door to the same
  screen and was removed.

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
