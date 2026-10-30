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
reads a photographed label), `printing` (printers, print agent and job queue), `monitoring` (system status
and availability alerts by email) and `carriers` (shipping companies the clients dispatch with: Andreani, Correo Argentino, OCA). Frontend: static multi-page app (`frontend/`), plain HTML/CSS/JS, no build
step.

Don't assume: there is no point-of-sale/destination catalog (`Address` stores city/state as free text),
the carrier APIs are Andreani's, Correo Argentino's and OCA's (`apps/carriers`, see below; for anything else `Order.carrier`/
`tracking_number` are typed in when dispatching), and the app is not scoped to one client or one carrier.

## Commands

All backend commands run from `backend/` with the venv active (`pip install -r requirements-dev.txt` for
development: it adds `zxing-cpp`, used only by the scanning tests, which skip without it):

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
- Demo data: `python manage.py seed_demo` (`apps/orders/management/commands/seed_demo.py`) — a merchant account
  (`demo@rotulos.local`, password in the command, `--email`/`--password`), an Empretienda store (the only platform
  that calls no API) with sender and rates, its orders in every status through `upsert_store_order`, two manual
  orders and an own address. Idempotent; refuses with `DEBUG` off unless `--force`.
- End-to-end check of the merchant's flow: `python manage.py test apps.orders.tests.test_commercial_flow` — only
  through the API, like the frontend: register → create business → account data → Empretienda store + spreadsheet
  import → label batch PDF → Andreani account (simulated) → quote + dispatch → rótulo + carrier label → pickup
  manifest → tracking to delivered → export + team activity. Run it before a test round with clients or a deploy:
  it catches breaks in the seams between apps that each app's own tests don't.
- ERD: `python manage.py erd_mermaid --output ../docs/erd.md` (`apps/accounts/management/commands/`) — the
  Mermaid `erDiagram` of every `apps.*` model, generated from the loaded models so it can't drift; the Django
  models they point to (`User`, `Group`) appear with only their PK. Regenerate it when models change.
- Template thumbnails: `python manage.py generate_template_thumbnails [--all]` — fills `LabelTemplate.preview`
  for active templates that lack one (see "Template thumbnails" under Labels).
- Lint: `flake8` (config in `backend/.flake8`). Frontend check: `node frontend/tools/check_frontend.mjs`.
  Both run in CI (see "CI (Azure Pipelines)").

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
`LOG_FORMAT=json` (default `text`) switches both handlers to `config/log_format.JsonFormatter`: one JSON object
per line with `ts`/`level`/`logger`/`message`, every `extra={...}` the code passes, and the traceback in
`exception` — for a log collector on a server. Stdlib only; any other value refuses to start.

### API errors are always JSON (`config/errors.py`)

DRF already answers its own errors in JSON and the frontend reads that shape (`getErrorMessage`), so that is
untouched. What used to come out as Django's HTML page was the unexpected: an exception in a view and an unknown
`/api/` route. `ApiExceptionMiddleware` (last in `MIDDLEWARE`, so first to see a view's exception) logs it under
`apps.errors` with a 12-char `error_id` and answers 500 `{detail, error_id}` — the merchant can quote the code
and it's in the log; the response is marked `_has_been_logged` so `django.request` doesn't log it a second time
without trace or code. `handler404`/`handler500` (in `config/urls.py`) answer JSON for `/api/` only; the admin
keeps Django's pages. With `DEBUG` on the exception goes through (the debug page is more useful while
developing).

### Health probes (`config/health.py`)

Two, not one, and the split is the point (a third, `health/system/`, covers the whole system — see
"Monitoring and alerts"). `GET /api/v1/health/` is **liveness**: it touches nothing and
answers "the process is up" — that is what an orchestrator polls to decide whether to restart the
container, so putting a DB query in it would restart the API on every Postgres hiccup, exactly when the
API is not the problem. `GET /api/v1/health/ready/` is **readiness**: it runs a real `SELECT 1` and
returns **503** when the database is unreachable, which is the one worth monitoring. Before, a single
probe returned `{"status": "ok"}` without touching anything, so it kept reporting healthy with the
database down.

### Monitoring and alerts (`apps/monitoring`, T#193)

Availability alerts **by email, from the app itself, with no external account** (decided 2026-10-19; an
external uptime monitor comes later, see the plan below). `checks.py` runs nine cheap checks, each `ok`,
`warning` or `error`: database, worker heartbeat (`MONITORING_WORKER_STALE_SECONDS`, 900 — generous because
the worker only beats between loops and a big label batch can take minutes), integration queue (a pending
event past its time by `MONITORING_QUEUE_DELAY_SECONDS` = the queue doesn't move; failed ones in 24 h are a
warning), abandoned background label batches, free disk on `MEDIA_ROOT`, whether alert mail can go out,
the alert process itself, stores in `error` and carrier accounts deactivated by an error. A crashing check
is reported as `error` and never stops the others. **Only `error` mails**; warnings show on the panel (a mail
for every small thing ends up ignored), and the mail check never mails (it would reach no one).
- **Alert on change, not on every pass** (`alerts.py`, state in `AlertState`): first mail after
  `MONITORING_ALERT_AFTER_SECONDS` (180) of grace — a restart wakes nobody, a failure fixed inside it is
  never mailed —, a reminder every `MONITORING_REMINDER_HOURS` (6), and a "Resuelto" mail. Everything from
  one pass goes in ONE mail. A failed send is not marked notified and is retried, but not before
  `MONITORING_MAIL_RETRY_SECONDS` (600). Recipients: `MONITORING_ALERT_EMAILS` if set, else active admins
  with email. With the database down there is no state nor admin list: only `MONITORING_ALERT_EMAILS` is
  mailed, once per reminder period per process.
- **Who runs it**: `python manage.py run_monitor` (every `MONITORING_INTERVAL_SECONDS`, 60; `--once`
  prints every check), the `monitor` service in `docker-compose.prod.yml`. It is separate from the worker
  on purpose — the first thing it must report is that the worker died. Both leave a `Heartbeat` row; the
  worker beats at most every `MONITORING_HEARTBEAT_EVERY_SECONDS` (`heartbeats.worker_tick`, called at the
  top of each loop and wrapped so monitoring can never stop the queue) and, **when the monitor hasn't beaten
  for `MONITORING_MONITOR_STALE_SECONDS`, runs the alert pass itself**. So one dead process never leaves the
  system silent; the whole server down does — that's the external monitor's job.
- **In local development** `.env` uses real SMTP: a local worker with the monitor stopped WILL mail every
  admin of the local DB after the grace period. Set `MONITORING_ALERT_EMAILS` to your own address there.
- API: `GET /api/v1/monitoring/status/` (`monitoring.view`, admin-only, migration `accounts/0025`): each
  check with detail, `failing_since`/`notified_at`, process heartbeats, recipients and the last mail's
  result; `POST /api/v1/monitoring/test-mail/` sends a test to the recipients (502 with the SMTP reason).
  Public `GET /api/v1/health/system/` (`config/health.py`): overall status + each check's status, **no
  details**, 503 only on `error`. It doesn't replace readiness: a dead worker is no reason to pull traffic
  from the API.
- Screen `estado_sistema.html` + `assets/js/estado_sistema.js` (admin sidebar "Estado del sistema", dashboard
  menu `system_status`), refreshed every 30 s while visible.
- **Planned, not done — external monitor** (when the user creates the account): point a free uptime
  service (UptimeRobot / Better Stack) at `https://<domain>/api/v1/health/system/` every 5 min, alerting on
  non-200 and on timeout, with the same admin email (and optionally Telegram/WhatsApp, which those services
  offer). It covers what nothing inside can: server, Docker or network down, and certificate expiry. Its
  public status page can be shown to merchants. Nothing to change in the code: the endpoint is already
  there and is throttled as anonymous (60/min).

### Merchant notices (`apps/monitoring/notices.py`, 2026-10-20)

The monitoring above tells the PLATFORM admin; this tells each business what broke in IT and won't fix
itself. `sync_all()` (run by `run_monitor` every pass, and by the worker when the monitor is stale) keeps one
open `MerchantNotice` per problem: a store in `error`, a carrier account deactivated with `last_error`, and in
the last `NOTICES_RECENT_DAYS` (3) a failed batch `Document`, a print job in `error` and a failed Tiendanube
`StoreLabelRequest`. A problem that's gone closes its notice (`resolved_at`); if it comes back it's a new one
(unique `(owner, key)` among open rows). New notices go in ONE mail per business and pass to the OWNER
(`NOTICES_MAIL_ENABLED`; a failed send is retried after `NOTICES_MAIL_RETRY_SECONDS`, 600) with a link to
`notificaciones.html`. Each notice carries the team `access` needed to see it and its `store_ids`
(`["unknown"]` for pre-teams batches), so `visible_notices(user)` shows a member only their areas and stores.
API `GET /api/v1/notices/`, `POST /api/v1/notices/<id>/dismiss/` (hidden for the whole business while the
problem lasts). Shown at the top of the dashboard (three newest) and in `notificaciones.html`. **Local `.env`
uses real SMTP**: a local `run_monitor` mails real owners of the local DB — set `NOTICES_MAIL_ENABLED=False`.

### Deploying (`docker-compose.prod.yml` + `Caddyfile`, repo root)

Brought up **by hand** — there is CI but no deploy pipeline (decided again 2026-10-19: no server yet; the
plan for T#181-185 is in `docs/despliegue.md`); deploying and automating the deploy are different things,
and the second only earns its keep after doing the first a few times. Six
services: Postgres, the API (migrations + `collectstatic` + `generate_template_thumbnails` then gunicorn;
API and worker log in JSON, `LOG_FORMAT=text` in `.env` to read them by eye), the integrations worker, the alert
monitor (`run_monitor`), the frontend, and Caddy, which terminates TLS with an automatic Let's Encrypt certificate — Tiendanube
refuses HTTP callbacks, so the certificate is not optional. Only Caddy publishes ports; the database is
not exposed at all, unlike the dev compose.
- Because Caddy serves the frontend and the API under **one domain**, nothing is cross-origin and CORS
  stops mattering in this layout.
- **Caddy serves only PUBLIC media directly** (store/account logos, template previews, saved-label logos and
  thumbnails, all with random or sample-only names). Everything else under `/media/` — batch PDFs
  (`documents/`), Tiendanube's labels (`store_labels/`), label photos (`rotulos/`) — carries buyer data and
  answers 404 there: it is downloaded only through the API (session or signed token). Found in the 2026-10-20
  security review: batches were `rotulos-<id>.pdf`, sequential, so anyone could enumerate every business's
  labels. Those files now also get a random part in their name (`rotulos-<id>-<hex>.pdf`;
  `Document.download_name` is the clean name the API serves) and uploaded photos a random name
  (`uploaded_label_path`). `test_private_files.py` pins the Caddyfile rule.
- **The API healthcheck sends `X-Forwarded-Proto: https`** on purpose: `prod.py` forces
  `SECURE_SSL_REDIRECT`, and the probe goes over plain HTTP to the container itself, so without that
  header Django answers 301 and the container would be marked unhealthy while being perfectly fine.
- `frontend/Dockerfile` is nginx with no build step (there is nothing to compile). Its entrypoint
  **generates `assets/js/config.js` from `API_BASE`** at container start: that file hardcodes
  `127.0.0.1:8000` for dev, which on a server would make the merchant's browser call *their own*
  machine. `.dockerignore` leaves out `assets/apis/` — 502 MB of the 504 the folder weighs, and nginx
  does not run PHP anyway — so the image is ~50 MB.
- **Asset versioning (2026-10-28)**: nginx caches `assets/` for 1 h, so after a deploy a browser kept the
  old CSS/JS. The Dockerfile (and the CI static artifact) runs `frontend/tools/version_assets.sh`, which
  rewrites every own CSS/JS reference in the `.html` to `?v=<md5 of its content>` (manual `?v=` included;
  `config.js` excluded), and nginx serves `.html` with `no-cache`. Nothing to stamp by hand; the repo
  files are never rewritten. Local dev (no nginx) can still show a stale file: hard reload.
- **Changing domain** (still the laptop's Tailscale tunnel as of 2026-10-29): the checklist is
  `docs/cambio-de-dominio.md` (server `.env`, Shopify TOML + `shopify app deploy`, Tiendanube Partner Portal,
  Google console, merchants' print agents). `python manage.py update_store_urls [--dry-run]` re-points the
  connected stores: enqueues `internal/store_setup` for each active one (webhooks with the new URL) and updates
  the Tiendanube carrier callbacks via `register_carrier` (which reuses the existing carrier). VTEX recognizes its
  own hook on the old domain by our secret in the `key` header and re-points it.
- Media (`MEDIA_ROOT`) is a real volume: the generated batch PDFs and store logos live on disk, so a
  platform with an ephemeral filesystem would lose them on every redeploy.
- **Backups (2026-10-29)**: `scripts/backup.sh [label]` (database `pg_dump -Fc` + the whole media folder,
  verified before keeping, rotation `BACKUP_KEEP`, optional copy to `BACKUP_S3_URI`) and `scripts/restore.sh
  <dump> [<media>]` (validates, asks to type RESTAURAR, backs up the current state first, stops api/worker/monitor,
  restores in one transaction). Run on the server from the repo root; `backups/` is gitignored. Details and the
  cron line in `docs/despliegue.md` → "Backups".
- The images use fully-qualified names (`docker.io/library/...`) so Podman resolves them too. Note the
  laptop has `docker-compose` 1.29.2, which is too old for these files (and for `backend/docker-compose.yml`
  as well) — Compose v2 (`docker compose`, no hyphen) is required.

### CI (Azure Pipelines, T#174-180)

Two pipelines, each triggered only by changes under its folder (branches `master`/`marco` and PRs to them);
the repo is on GitHub, so each is created in Azure DevOps as "Existing Azure Pipelines YAML file". **CI only:
nothing is deployed** and there is no image registry, so images are published as pipeline artifacts
(`docker save | gzip`, tagged with the commit and `latest`; `docker load` on any machine). In a PR only the
checks run.
- `backend/azure-pipelines.yml`: Python 3.13, `pip install -r requirements-dev.txt`, `flake8`,
  `makemigrations --check`, `coverage run manage.py test` → `coverage.xml` published, then the API image.
  The agent has no `.env`: only `SECRET_KEY`, `DJANGO_SETTINGS_MODULE=config.settings.dev` and `LOG_LEVEL`
  come from the pipeline, and the whole suite was run that way (a copy without `.env`). SQLite, like local.
- **Lint = real errors only** (`.flake8`: `E9` + pyflakes `F`, tests may keep unused imports/locals). Not
  black/pycodestyle: the code never followed a formatter and enforcing one would rewrite ~300 files at once.
- `frontend/azure-pipelines.yml`: `tools/check_frontend.mjs` (no dependencies: every `assets/**/*.js` and
  every inline `<script>` must pass `node --check`, and every relative `src`/`href` in a page must exist;
  skips `assets/apis/` and `index_test.html`), then the static files (what goes in the image, ~1.6 MB) and
  the nginx image as artifacts.
- The backend `.dockerignore` excluded `.venv/` but the virtualenv is `venv/`: the API image was carrying
  ~280 MB of it plus the `db.sqlite3*` recovery copies. Fixed with the CI; pipelines and lint configs are
  excluded from both images too.

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

No build step. Serve `frontend/` with any static server (launch config `frontend`, port 8001): opened as
`file://` the pages render but **login doesn't work**, because the session lives in cookies (see "Session in
HttpOnly cookies") and a `file://` page has no site to send them to. The API base URL
lives only in `assets/js/config.js` (`window.APP_CONFIG.API_BASE`), loaded first on every page; every other
script builds URLs on it. Opened from `localhost`/`127.0.0.1` it is port 8000 of **that same host name**
(`localhost:8001` → `localhost:8000`): the browser treats `localhost` and `127.0.0.1` as different sites
and would not send the cookies across;
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
  `admin/`, `render/`, `preview/`, `barcode/`, `batch/`, `fonts/`, `sequence/`)
- `api/v1/integrations/` → `apps.integrations.urls` (admin ABM + store connections, plus
  `<platform>/install-url|callback|webhooks/` per provider and `shopify/launch/`)
- `api/v1/monitoring/` → `apps.monitoring.urls` (`status/`, `test-mail/`, admin; see "Monitoring and alerts")
- `api/v1/team/` → `apps.accounts.team_urls` (the merchant's team; see "Teams"); the admin's
  `users/<id>/team/` and `teams/` live in `user_admin_urls`
- `api/v1/printing/` → `apps.printing.urls` (`agents/`, `printers/`, `jobs/`, `agent/download/`, and the agent's own
  `agent/jobs/next|<id>/file|<id>/result/`, see "Direct printing")
- `api/v1/carriers/` → `apps.carriers.urls` (common: ``, `accounts/<carrier>/`, `branches/<carrier>/`, `shipments/...`,
  `push/<carrier>/<key>/`; plus Andreani's original `andreani/...`)
- `api/v1/ingest/` → `apps.integrations.ingest_urls` (API-key/webhook order ingest, separate auth; its OpenAPI 3
  contract at `GET openapi.json`, public, built by `ingest_openapi.py` from `ingestion.TARGET_FIELDS` and the
  `ingest` throttle rate so it can't drift — what can't be derived (per-item result, webhook signature) is
  written once and tested)
- `api/v1/integrations/` → `apps.integrations.providers.tiendanube.label_urls` and `.rate_urls` (Tiendanube Labels API callbacks + public PDF
  download, separate auth — see "Labels the store asks for" below)

REST Framework is closed by default (`DEFAULT_PERMISSION_CLASSES = [IsAuthenticated]`); a public endpoint
needs `permission_classes = [AllowAny]` explicitly. JWT only (`djangorestframework-simplejwt`), access
token in `Authorization: Bearer <token>` **or** in the `rotulos_access` cookie (the web frontend, below).

### Session in HttpOnly cookies (`apps/accounts/auth_cookies.py`, T#364/T#366)

The web frontend no longer keeps tokens in `localStorage` (readable by any script on the page). It sends
`X-Auth-Mode: cookie` on every request (`Auth.withSession`, with `credentials: "include"`); with that header
login/register/Google/refresh put the tokens in cookies and **remove them from the JSON body** (else a script
could call refresh and read them anyway). Without the header everything answers as before — API clients and
the whole test suite use Bearer + body tokens. Decisions, as asked in T#366: names `rotulos_access` (path
`/api/`) and `rotulos_refresh` (path `/api/v1/auth/`, the only place it's used: refresh and logout); no
domain (host-only; `AUTH_COOKIE_DOMAIN` if ever needed); HttpOnly; SameSite=Lax; Secure =
`AUTH_COOKIE_SECURE`, default on unless `DEBUG`; max-age = the token's lifetime (15 min / 7 days).
`JWTAuthenticationWithPasswordPolicy` reads the header first, then the cookie; an invalid cookie is ignored
(a stale one must not break login/refresh, which receive it too), and **an unsafe method authenticated by
cookie requires `X-Auth-Mode`** (403 otherwise) — a custom header forces a CORS preflight, so another site
can't forge a write. `CookieTokenRefreshView` takes the refresh from the body or the cookie (rotation +
blacklist unchanged) and, in cookie mode, a dead one answers 401 and clears the cookies; logout revokes the
cookie's refresh and clears both. CORS: `CORS_ALLOW_CREDENTIALS` and `x-auth-mode` allowed (and `x-team-id`, the current business — see Teams). A browser
holding an old `localStorage` refresh trades it once for cookies on load (`auth.js`), so nobody is logged
out by the change. Tests: `apps/accounts/tests/test_auth_cookies.py`.

### Shared across apps (`apps/common`)

A plain package, not a Django app (no models, not in `INSTALLED_APPS`). `date_filters.py` holds the
`?date_from=`/`?date_to=` contract (AAAA-MM-DD, **both inclusive**): `parse_date_param`,
`parse_date_range` and `date_range_q(query_params, field="created_at", ...)`, which returns a `Q` — empty
when no dates came, so a caller filters with it unconditionally. Seven views across six apps each carried
their own copy, five of them identical character for character, with two different error messages and one
endpoint that silently accepted a reversed range.
`spreadsheet.py` holds the downloadable-sheet rules every export follows: `csv_bytes(columns, rows)` (`;` + BOM,
what Spanish Excel opens on double click), `xlsx_bytes(columns, rows, title)` (text forced to string) and
`safe_cell` (values starting with `= + - @` neutralized — CSV injection). Used by the order export and the audit
export. The comparison is always `__date__gte`/`__date__lte` on
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
  only renders it. **Since 2026-10-20 the dashboard draws `sections`, not `menu`** (user: "hay muchos botones
  y se terminó creando un menú larguísimo"): `dashboard_sections.build_sections(user)` groups the 19-23 entries
  into six areas — Pedidos, Imprimir, Diseño de rótulos, Tiendas y envíos, Mi comercio, Ayuda (+ Administración
  for the admin) — each opening its main screen and listing its other tools as `links`, every one filtered by
  `user_has_permission`. **Symmetric on purpose** (asked 2026-10-21): every card has the same shape — header,
  a list of tool rows with room for three, "Abrir <sección>" full width at the bottom — so they're all the same
  height; `dashboard.js` picks the columns from the count (4 → 2×2, otherwise up to 3) and a short last row is
  centred; Administración is a full-width strip under the grid. Keep each section at three tools at most (Mi
  cuenta shows Equipo/Perfiles/Actividad to whoever manages the team, else shortcuts to `#perfil`/`#comercio`;
  Ayuda: FAQ, write to support, notices). The same tools appear in that main screen's topbar (`data-links` items may carry
  `"perm"`, which `topbar.js` checks against the stored session). `menu` is still sent (its tests and anything
  reading it). `onboarding(user)`: four first steps (store, business data, template, carrier) computed from
  the data of the current business, only for its OWNER who isn't the admin; "Ocultar" is per browser (localStorage). Icons:
  the backend still names each section's icon with its Material Design name (`mdi mdi-printer`…); `dashboard.js`
  (`SECTION_ICONS`/`sectionIcon`) translates them to Phosphor, the app's icon set (see "Design system") — a new
  section icon needs an entry there. **Function search** (2026-10-21, above the
  cards; `setupToolSearch` in `dashboard.js`): searches every tool the user can reach — the sections and their
  links plus the full `menu`, one entry per page (a second name for the same page only widens the match), plus
  `IN_PAGE_TOOLS`, actions that are a button inside another page (planilla de retiro, cargar seguimientos, nuevo
  pedido, direcciones…, each gated by `sessionHasPermission`; **rename a button → rename it there too**).
  Accent/case-insensitive, all words must match; arrows, Enter opens, Esc clears, "/" focuses it. `profile_views.ProfileView` is the self-service `me/` GET/PATCH (screen: the top half of `organizacion.html`,
  "Mi cuenta").
- **Own activity** (`GET /api/v1/team/activity/mine/`, `MyActivityView`, `users.me.view`, 2026-10-22): any member
  sees what THEY did in their current business (same rules as the team's Activity, `actor` forced to them); page
  `mi_actividad.html` + `assets/js/mi_actividad.js`, linked from Mi cuenta (topbar, and the dashboard card for whoever
  doesn't manage the team). The admin sidebar's "Estado del sistema" gets a red/amber dot when `/health/system/` says
  error/warning (`admin_sidebar.js`, at most once a minute per tab).
- **Personal profile (`PersonalProfile`, `personal_profiles.py`, 2026-10-22)**: what each PERSON customizes about
  themself, not the business — `preferred_name` (shown as `display_name`, else first+last name, else email),
  `phone`, `job_title` and an own `photo` (file or data URL, 2 MB, cropped to a 256 px square JPEG without EXIF,
  random name under `media/accounts/avatars/`, public in Caddy like the logos; `null` removes it and the old file
  is deleted). Optional row, created on the first save; edited through `me/` (`ProfileSerializer`). `user_payload`
  carries `display_name` and `photo` (which wins over Google's `picture`); the team's `members` and each
  `activity/` row's `actor` carry `public_fields` (name, job title, phone, photo). Frontend: `personAvatar`/
  `personAvatarUrl` in `utils.js` (photo → Google → initials drawn locally by `initialsAvatarUrl`; it used to be api.dicebear.com with the EMAIL in the URL, which sent every person's email to a third party on every page — 2026-10-29), the topbar, `equipo.js`, `actividad_equipo.js`.
  Tests: `apps/accounts/tests/test_personal_profile.py`.

### Roles & permissions (`apps/accounts`)

There is **no `role` field on `User`** — it's derived, never stored directly:

- **Two fixed roles since 2026-10-21** (user: "es lo mismo que equipos"): `admin` and `subscriber`, shown as
  **"Comerciante"** (the business owner; the key is unchanged, stored data is never renamed). `designer` and
  `operator` were merged into `subscriber` by migration `accounts/0030` (their users moved; admin permissions
  added to them by hand were dropped, decided with the user) and are now aliases of it in `LEGACY_ROLE_ALIASES`,
  so a leftover Group with that name counts as a merchant; they stay protected names (a custom role called
  "operator" would fall into subscriber). Any other Group is a custom role, presented as **platform staff**
  ("Personal de la plataforma" in `roles.html`, purple `staff` badge in `gestionuser.html`): a slice of the admin
  such as support or reports. What an employee can do inside a business is the team profile's job, not the role.
- **The admin is the "leviathan" (2026-10-21, user's word)**: `_role_has_permission` returns True for every
  permission when the effective role is `admin`, without reading `GroupRolePermission` — a new permission reaches
  it with no migration, and it can't be locked out. Its role can't be edited (`RolePermissionsView.put` → 400
  `ADMIN_ROLE_LOCKED_MSG`; its GET returns the whole catalog), and **nobody edits the permissions of a role they
  have** (`OWN_ROLE_LOCKED_MSG`). `RoleSerializer` exposes `locked`/`locked_reason` and `roles.html` shows such a
  role read-only with the reason. Deleting a custom role moves to `subscriber` only the users left with no role.
- `permissions_map.get_effective_role()` is the source of truth for "which role": checks canonical Django
  Groups (`admin`, `subscriber`) first, then falls back to a custom Group name
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
- **Since teams, `user_has_permission` = role ∩ team** (see "Teams"): the role is the ceiling the admin sets in
  `roles.html`; for the keys in `permissions_map.TEAM_SCOPED_PERMISSIONS` (the merchant's work) it ALSO needs
  the matching team access. Keys not in that map (own profile, support, everything admin) are role only.
  `orders.create` used to gate stores, carriers, rates and dispatch at once; it was split (migration
  `accounts/0028`) into `orders.dispatch`, `orders.export`, `stores.manage`, `carriers.manage`,
  `printers.manage`, `account.manage`, `team.manage` — every group that had `orders.create`/`labels.batch`/
  `users.me.edit` got the matching new ones, and `subscriber` also got `orders.create_manual`/`orders.import`/
  `orders.import_mappings` (a team owner has everything of their business).
  **Split again in migration `accounts/0029` (2026-10-21, user: "pedidos, rótulos y admin más finos")**, each
  group that had the old key got the new ones: `orders.tracking_import` and `orders.manifest` (from
  `orders.dispatch`; team access "Despachar"), `labels.template_edit` (update + restore version) and
  `labels.template_delete` (from `labels.template_create`, which keeps create/duplicate; the template serializer
  exposes `can_edit`/`can_delete` and `mis_plantillas.html` follows them), `users.delete` (`?permanent=true`; plain
  deactivate stays `users.deactivate`), `users.export`, `audit.export`, and `reports.view` (the four metrics
  endpoints and the Reportes page/sidebar — reports without the user list). New team access "Borrar lotes"
  (`documents.delete`, before part of "Imprimir rótulos"): existing profiles/members/pending invitations that
  printed got it; the suggested "Depósito" of NEW businesses doesn't (prints, doesn't delete).
  **The stored session refreshes its permissions**: `auth.js` re-reads `me/` in the background on page load (at
  most every 2 min, `sessionStorage.userCheckedAt`) and fires `auth:user-updated` (the admin sidebar re-renders).
  Before, a role change in `roles.html` or a new permission only reached a browser after logging in again.

### Account data and global catalogs (T#351-356)

**Account data (`AccountSettings`, `apps/accounts/models.py`).** Decided 2026-10-16 as the account of one user;
since 2026-10-19 it is **the business's** data: the row hangs from the business's data user (like every business row,
see "Teams") — read by anyone in the team, edited with `account.manage`. One optional row, created on the
first save: `legal_name`, `tax_id` (CUIT, validated with AFIP's check digit — `cuit_is_valid` — and stored as
`XX-XXXXXXXX-X`), `sender_name`/`sender_address`/`sender_phone`, `logo`, and preferences `default_template`,
`default_carrier` (an API carrier code or a `ManualCarrier` name) and `default_print_format` (label|a4|zpl).
`GET/PATCH /api/v1/auth/me/account/` (`users.me.view`/`account.manage`, audited `account.update`), screen
`organizacion.html` (menu `account_settings`). **Since 2026-10-21 that page is "Mi cuenta"**: the person's own
profile and password (`perfil.js`, endpoint `me/`) on top and the business data (`organizacion.js`, wrapped in
its own scope because both scripts declared `API_BASE`/`apiFetch`) below, anchors `#perfil`/`#comercio`.
`perfil.html` only redirects there (old links; the admin sidebar's "Ajustes" now points straight to it). Where it's used:
- **Sender**: `label_rendering.sender_fields(owner, store)` — the store's sender, else the account's, else the
  store's name, else the legal name; address/phone the store's else the account's. Used by
  `build_label_context`, `build_shipment_context` (Tiendanube Labels API) and the pickup manifest
  (`manifest_sender`). `sender_logo(owner, store)`: the store's logo else the account's (batch, store print,
  Tiendanube labels). A store's own data always wins.
- **Manifest** shows "Razón social: X · CUIT Y" when loaded.
- **Default template**: after the store's (batch `_resolve_template`, `store_print.resolve_template`), before
  the oldest public one.
- **Front-end defaults** (`loadAccountSettings()` in `utils.js`, one request per page): the dispatch dialog
  starts on `default_carrier` if that carrier is ready (else the last used, else the first);
  `imprimir_rotulos.html` starts on `default_print_format` unless there is a default printer.

**Global catalogs, admin-managed (`catalogs.manage`, admin-only, migration `accounts/0024`).** Shared base
`apps/common/catalogs.py` (`CatalogViewSet`): any authenticated user READS active entries, writing needs the
permission, `?include_inactive=1` only for managers, delete = deactivate, audited `catalog.update` (category
`catalogs`). Screen `catalogos.html` + `assets/js/catalogos.js` (admin sidebar "Catálogos").
- `ManualCarrier` (`apps/carriers`, `/api/v1/carriers/manual-carriers/`): carriers WITHOUT API, with an
  optional `tracking_url_template` containing `{tracking}`. A name equal to an API carrier is refused. Every
  free-text carrier field (despachar, estado_pedidos, planilla, seguimientos, both dialogs in Mis pedidos) gets
  them as suggestions (`attachCarrierSuggestions` in `utils.js`, a shared `<datalist>`), and
  `orders.shipping.apply_shipping` stores the catalog's spelling and builds the tracking URL when none came.
- `LabelSize` (`apps/labels`, `/api/v1/labels/sizes/`, seeded with 10×15, 10×10, 10×5 and A6 in
  `labels/0012`; same 5-30 × 5-40 cm limits as a template): the editor's "Medida" select
  (`assets/js/label_editor_sizes.js`) fills width/height; typing numbers that match none shows "Otra medida".

### Teams — businesses with an owner and employees (`apps/accounts/teams.py`, 2026-10-19; several per person 2026-10-23)

Decided with the user (spec `docs/superpowers/specs/2026-10-19-equipos-design.md`, plan
`docs/superpowers/plans/2026-10-19-equipos.md`): a business ("comercio") is a team — one **owner** and zero or
more **employees**, each with their own login, all working on the same stores, orders, templates, carrier
accounts, printers, rates and business data. **Since 2026-10-23 (stage 1 of "varios comercios por persona") a
person starts with NO business and can be in several**, with a different role in each (owner of one, employee of
another), creating as many as they want. Two businesses never merge. **A store belongs to ONE business**: sharing
a store between businesses was considered as a stage 2 and dropped (2026-10-23, the user's boss: it makes no sense).
- **Data hangs from each business's DATA USER** (`Team.data_user`, OneToOne, PROTECT): an internal user
  (`comercio-<hex>@datos.invalid`, inactive, unusable password) — none of the 24 `owner`/`user` FKs changed.
  `Team.owner` is the PERSON (FK, nullable only for a closed business whose owner was deleted). Every view that
  reads or creates business data uses **`teams.data_owner(request.user)`** = the data user of the CURRENT
  business. What stays `request.user`: permission checks, the audit actor, authorship fields (`created_by`,
  `uploaded_by`, template versions), support messages, profile and session. Models keyed only by authorship
  stay personal (photo imports `LabelImport`/`UploadedLabelFile`, System 2 `ElementLayout`/`LayoutVariable`).
  The OAuth `state` and the Tiendanube share link carry the data user (= the business), so a store connected by
  an employee belongs to the business; `read_oauth_state`/`read_share_token` and print agents accept it via
  `teams.live_data_owner_q` (an active person OR the data user of an OPEN business) instead of `is_active`.
- **Data users are not people** (`teams.internal_users_q()`): hidden from the user admin (list, summary, CSV,
  bulk actions, detail → 404, metrics — `user_admin_views.people()`), never mailed. Whatever used the owner as a
  person goes through helpers: `mail_person(data_user)` (merchant notices, the privacy `customers/data_request`
  report: the owner person; the notice mail names the business), `business_display_name(user)` (label/manifest/
  export fallback when there is no sender or recipient name), `contact_email(user)` (admin serializers'
  `owner_email`/`user_email`), `team_of_data_user(user)`. Order metrics count businesses, not users.
  `account.update` is audited against the team. A background label batch carries `view_all` from the requester
  (the document's user is the data user).
- **Current business of a request**: the frontend sends `X-Team-Id` (per tab, see Frontend); `TeamCacheMiddleware`
  puts it in a `ContextVar` and `membership(user)` uses it if the person is a member of that OPEN business; else
  (no header, forged or stale one) the one with the latest `TeamMember.last_used_at` (set by `switch/`, create and
  accept), else the newest. **`membership()` never creates anything: no business → `None`**; `data_owner`/`team_for`
  raise `teams.NoTeam` (403 `{"detail", "code": "no_team"}`), team-scoped permissions are False and
  `HasRolePermission` raises `NoTeam` for them, so the frontend sends the person to `comercio_inicio.html`. Inside a
  request membership rows are read once (`request_cache()`), outside (worker, shell) every call reads the DB;
  whatever changes a membership calls `teams.forget(user)`; `teams.use_team(id)` switches the request (switch,
  create and accept answer the user seen from the new business). Per-member helpers for OTHER people of a business:
  `accesses_of(member)`, `stores_of(member)`. The Tiendanube print-link (opened by Tiendanube in a new tab) first
  moves to whichever of the person's businesses owns that store.
- The platform admin needs no business: the administration works without one; to work as a merchant they create or
  join one like anybody.
- Models (`team_models.py`, imported from `models.py`): `Team` (owner, data_user, name, `join_code` +
  `join_code_enabled`, closing fields), `TeamMember` (FK user, unique `(team, user)`, one OWNER per team, role
  owner|employee, `profile` or custom `accesses`, `all_stores` + `stores`, `last_used_at`), `TeamProfile` (named
  access set per business; every business starts with "Todo", "Depósito", "Atención"), `TeamInvitation`,
  `TeamJoinRequest` (pending|approved|rejected|cancelled, one pending per team+user). `teams.create_team(owner,
  name, data_user=None)` makes the data user, the owner membership, the profiles and the code. Migrations
  `accounts/0033-0035`: open businesses with NO data and no other member were deleted (those people see the start
  screen); the rest got a data user and their `OWNED_DATA` + `MerchantNotice` rows moved to it; closed ones use their
  former archive user as data user and `owner` = the old `former_owner` (field removed). `0034` doesn't undo
  (reverse is a no-op). Tests: `test_multi_team.py`, `test_multi_team_migration.py`. **Tests build the real
  shape** (2026-10-26, replacing the old "solo" shortcut where the data user was the person itself):
  `team_helpers.new_business(person)` creates the business and returns its data user; a test creates it BEFORE
  the data and hangs stores/orders/templates/accounts from that data user, while the person still calls the API
  and is the audit actor. A store's OAuth `state` carries the data user as `u` and the person who asked as `a`
  (`oauth_state_actor`), so the callback audits the person, not the internal user.
- **The 12 accesses** (`teams.ACCESSES`, the merchant's words): ver pedidos, imprimir rótulos, despachar,
  cancelar, cargar e importar, plantillas, tiendas, transportistas y tarifas, impresoras, datos del comercio,
  equipo, exportar. Owner = all; employee = their profile's (a profile change applies to everyone on it) or
  their custom ones. `permissions_map.TEAM_SCOPED_PERMISSIONS` maps each permission key to its access.
  Reading lists that other screens need only requires seeing orders (stores list, `/carriers/`, shipments
  GET); the carrier account GET needs dispatch; printers GET needs printing.
- **Stores per employee**: "all stores" (default, also future ones) or a list. A limited employee sees only
  orders of their stores **plus manual orders**, and only those stores (settings, rates, checkout, Empretienda
  import, Tiendanube print link). Helpers: `allowed_store_ids`, `order_scope_q(user, prefix)`,
  `store_scope_q(user, field)`, `orders_outside_scope`, `visible_document_ids`. A batch by ids with an order of
  another store → 400 (`OUTSIDE_SCOPE_MESSAGE`), and so does the pickup manifest (an incomplete manifest
  would be signed by the carrier); by filters it is silently narrowed. `Document.store_ids`
  (filled by the batch; `null` for batches made before teams = unknown) hides a document — and its print jobs —
  containing orders of other stores, or of unknown stores. The tracking-import preview, `/labels/render/`,
  the label↔order link and rates writes are scoped too. **Careful:
  `Q(...) | Q()` returns the first Q alone** — an empty scope must not be OR-ed (see the `Label` list).
- **Joining** (`team_services.py`), always ADDS a business (never removes the others):
  - **Invitation by email**: the owner (or anyone with the "Equipo" access) invites (`POST /team/invitations/`,
    mail with a link to `invitaciones.html`, `TEAM_INVITATION_DAYS` = 7; invite and resend share the `team_invite`
    throttle, `TEAM_INVITE_THROTTLE_RATE` 30/hour, because each one mails any address; the mail goes INSIDE the
    transaction, so a failed send leaves no invitation and answers 502); the person must **accept** with a verified
    email equal to the invitation's (`/team/my-invitations/<id>/accept|decline/`; the accept answers the `user` seen
    from the joined business).
  - **Fixed code/link of the business** (`Team.join_code`, 10 chars without ambiguous ones): seen, switched off and
    regenerated in Equipo → Miembros (`GET/PATCH /team/join-code/ {enabled}`, `POST /team/join-code/regenerate/`).
    Whoever uses it (`POST /team/join/ {code}` from `comercio_inicio.html` or `unirse.html?code=`; dashes/case/a
    pasted link are tolerated; throttle `team_join`, `TEAM_JOIN_THROTTLE_RATE` 10/hour; a wrong and a disabled code
    answer the same) is PENDING until someone with "Equipo" approves choosing profile and stores
    (`POST /team/join-requests/<id>/approve|reject/`, same no-escalation as inviting) — `GET /team/` lists them in
    `join_requests`. The person sees/cancels theirs at `GET/DELETE /team/my-join-requests/[<id>/]`.
  - The platform admin adds directly, no acceptance (`POST /users/<id>/team/ {team_id, role}`); `GET` lists every
    business of the user.
  - **Create** (`POST /team/create/ {name}`, any logged-in person), **switch** (`POST /team/switch/ {team_id}`) and
    **leave** (`POST /team/leave/`, an employee; the owner must transfer or close first; what they did stays).
  **No escalation**: whoever hands out accesses can only give accesses/stores they have IN THAT business, never touch
  themself or the owner, and (if not the owner) never touch a member with accesses or stores they lack. Removing an
  employee only deletes the membership. **Transfer** (`POST /team/transfer/`, owner only) only swaps roles and
  `Team.owner` (data stays on the data user); the old owner stays as employee with "Todo".
  **Closing the business = soft delete** (`POST /team/close/ {confirm: "BAJA", reason}`, owner only,
  `team_services.close_team`): nothing is deleted — the data stays on the data user and every membership is removed,
  so nobody sees it; each person keeps their account and their other businesses (none is created). Stores are
  revoked and their `external_store_id` gets `#baja-<team id>` so the same store can be connected again as a NEW
  row; carrier accounts, print agents, API keys and webhooks are switched off (ids in `closed_state`), pending print
  jobs, invitations and join requests cancelled, the code switched off; `closed_at`/`closed_by`/`close_reason` keep
  who and why. The admin lists them at `GET /teams/closed/` and gives one back with `POST /teams/<id>/reopen/`
  (`reopen_team`, `users.edit`) as one MORE business of its owner: data, store ids (unless taken meanwhile →
  warning) and switched-off rows come back, employees don't (re-invited), stores need reconnecting (tokens were
  dropped). Screens: block at the bottom of Mi cuenta (`baja_comercio.js`) and admin `comercios_baja.html`. Audited
  `team.close`/`team.reopen`. Tests `test_team_close.py`, `test_multi_team.py`. The admin's permanent user delete
  refuses an owner of an open business with employees (transfer first) and otherwise deletes their open businesses
  and their data users with them; closed ones keep their data, owner set to null.
- API `/api/v1/team/` (`team.manage`): `GET` (members, pending invitations, profiles, catalog, stores, `me`),
  `PATCH {name}`, `members/<id>/` PATCH/DELETE, `invitations/` (only pending AND not expired are listed) +
  `<id>/` DELETE + `<id>/resend/`, `profiles/` (creating one also respects no-escalation)
  CRUD (in use → 400), `transfer/`, member PATCH is a real PATCH (what isn't sent stays), `activity/`. **Activity per
  business**: `AuditLog.team_id` (plain int, `audit/0012`) is filled by `audit.services.record` (the target's id if it
  is a Team, else `teams.audit_team_id(actor)`: the data user's business or the actor's current one), so `activity/`
  and `activity/mine/` show the rows of THIS business only; rows written before (team_id null) keep the old rule
  (current members, since they joined, business categories plus `account.update`). Without IP/user agent.
  `user_payload` (and `me/`, and the dashboard response) carries `team` = the current one {id, name, display_name,
  role, accesses, all_stores, store_ids} or `null`, and `teams: [{id, name, display_name, role}]`. All changes
  audited in category `team` (`team.create`, `team.join_request|join_approve|join_reject|join_code_update`,
  `team.leave`, ...).
- **Profile editor shared with Roles**: `createProfileEditor(root, {catalog, allowed, save, remove})` in `utils.js`
  (styles `.pe-*` in `ui.css`) draws profiles on the left and the chosen one's name + accesses on the right, with
  Guardar/Descartar only on changes — the same layout as `roles.html`. Used by the Perfiles tab of `equipo.html`
  (`allowed` = the editor's own accesses) and by `roles.html` → Perfiles de los comercios → "Editar perfiles"
  (`roles_perfiles.js`, `allowed` null): the platform admin edits any business's profiles through
  `PATCH/DELETE /api/v1/teams/profiles/<id>/` and `POST /api/v1/teams/<team_id>/profiles/` (`users.edit`), which
  share `create_profile`/`update_profile`/`delete_profile` with the team's own views (same rules: unique name,
  a profile in use isn't deleted; only the "can't grant what you don't have" limit is lifted).
- Frontend: `equipo.html` with tabs (2026-10-21, the user found three pages confusing) — Miembros (members,
  invite, edit accesses/stores, transfer; `equipo.js`, which also drives the tabs, `#miembros|#perfiles|#actividad`),
  Perfiles (`perfiles_equipo.js`) and Actividad (`actividad_equipo.js`), each script in its own IIFE because they
  share the page; a profile change fires `team:profiles-changed` and the Miembros tab reloads. `perfiles_equipo.html`/
  `actividad_equipo.html` only redirect to their tab (old links; the dashboard points straight to `equipo.html#perfiles`/`#actividad`).
  `invitaciones.html` (the invited person; the dashboard shows a notice when there is one),
  admin `comercio_usuario.html` (linked from each row of `gestionuser.html`). Shared
  `renderAccessCheckboxes`/`readAccessCheckboxes`/`renderStoreChoice`/`readStoreChoice` in `utils.js`, styles in
  `equipo.css`. An invitation starts on the profile with FEWEST accesses. Menu entries follow the accesses
  (`DashboardView`). Miembros also shows "Código para sumarse" (copy link, switch off, regenerate) and "Pedidos para
  sumarse" (approve with the member dialog, reject).
- **Several businesses in the frontend**: `auth.js` keeps the current business PER TAB (`sessionStorage.teamId`;
  `localStorage.lastTeamId` is where a new tab starts) and `withSession` sends it as `X-Team-Id` on every request;
  the stored `user` is also per tab (sessionStorage first, localStorage as the last used). `saveSession` syncs
  `teamId` with `user.team`; `Auth.switchTeam(id)` posts `switch/` and reloads. A 403 `code: no_team` sends to
  `comercio_inicio.html` except on the pages usable without a business (comercio_inicio, unirse, invitaciones,
  organizacion, ayuda). The selector is `renderTeamSwitcher(container, user)` (`utils.js`, styles in `ui.css`): in the
  shared topbar and in the dashboard header; its last option goes to `comercio_inicio.html`. The dashboard without a
  business sends a merchant to `comercio_inicio.html` (the admin gets a notice instead).
  `comercio_inicio.html` (`comercio_inicio.js`): create, pending invitations (`renderMyInvitations`, shared with
  `invitaciones.js`), paste a code, own pending requests. `unirse.html?code=` (`unirse.js`): without a session keeps
  `localStorage.pendingJoinCode` and `index.html` comes back after login. Mi cuenta: "Salir de este comercio" for an
  employee (`salir_comercio.js`); without a business its "Datos del comercio" part is replaced by a link to start one.
- Local testing: `.claude/launch.json` → `backend-sin-mail` runs the API with mails to the console (the local
  `.env` uses real SMTP and an invitation sends a real mail).

### Audit trail (`apps/audit`)

- `AuditLog` is the single, immutable model (`save()`/`delete()` refuse to touch an existing row): `actor`
  (FK, `SET_NULL`) + `actor_email` snapshot, `category`/`action` (`TextChoices`, one category per action via
  `ACTION_CATEGORIES`), `target_type`/`target_id`/`target_repr` (plain fields, no
  `GenericForeignKey`/contenttypes), a `changes` diff (`{"field": {"from", "to"}}`, never
  passwords/hashes/tokens), IP/user-agent, `created_at`.
- `GET /audit/logs/` (filters: `search`, `category`, `action`, `actor`, `target_type`, `date_from`/`to`,
  `ordering`; permission `audit.view`), `GET /audit/logs/export/?file_type=csv|xlsx` + the same filters
  (`filter_logs`, shared, so the file is exactly what the screen shows; capped at `AUDIT_EXPORT_MAX_ROWS`, 10 000,
  count/cut in `X-Audit-Count`/`X-Audit-Truncated`; "Descargar CSV/Excel" in `auditoria.html`), `GET /audit/actions/` (catalog for frontend selects), `GET
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
- `GET/POST /orders/`, `GET/PATCH /orders/<id>/`, `POST /orders/<id>/ship/` (`orders.dispatch`, forward-only
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
    shipping statuses `orders.dispatch`.
  - `GET /orders/export/?file_type=csv|xlsx` + the list's own filters (`views.filter_orders`, shared with
    `GET /orders/`) or `ids=`. **`file_type`, not `format`**: DRF reserves `?format=` for the renderer and
    404s on "csv". CSV is `;` + BOM (what Spanish Excel opens on double click); third-party text starting
    with `= + - @` is neutralized (CSV injection) and xlsx cells are forced to string. Count in
    `X-Order-Count` (exposed via `CORS_EXPOSE_HEADERS`, like `Content-Disposition`).
  - `POST /orders/manifest/` `{order_ids, carrier?}` → "Planilla de retiro" PDF (platypus table with
    repeated header + signature block kept together) the carrier signs on pickup. Same rule as the label:
    no products, prices or buyer contact. Changes no status.
  - Every spreadsheet goes through `import_parsing.parse_tabular_file`, which stops at
    `IMPORT_PARSE_MAX_ROWS` (20 000) WHILE reading — above each screen's own limit (1000 import, 5000
    Empretienda): a 5 MB .xlsx can hold millions of rows and used to be loaded whole before any check.
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
`line-height: 1`, and one line (the renderer truncates with an ellipsis) unless the text sets `max_lines` > 1. **If those constants
change in `label_rendering.py`, change them in the editor too.** `renderDecorations()` also draws the
`border`/`lines`/`texts` the renderer adds, and `applyRenderMetrics` honours per-field `font_size`/`bold`/
`align`/`width` — the seeded template uses all of them, and none of it used to show.
- The safety net is `POST /api/v1/labels/preview/` (`PreviewLabelView`, `labels.render`): it renders a
  design that has **not been saved** — `{design, width_cm, height_cm, template_id?}` — with
  `build_preview_context()`'s deliberately long sample values, and returns the PDF. The editor's "Ver cómo
  sale impreso" shows it in a modal. When the canvas and the PDF disagree, the PDF is right.
- **Layout** (2026-10-16): canvas on the left, a sticky inspector on the right (`.workspace` grid, one column
  under 1000px) with panels "Elemento seleccionado" (the code/field/decoration/lock groups — only the chosen
  element's panel shows; `selectField` hides the others), "Rótulo", "Decoraciones" and "Guardar y exportar";
  "Guardar rótulo" and "Ver cómo sale impreso" at the top. Same palette and font as the rest of the app (the
  design-system tokens, Geist); the label itself uses Helvetica/Arial, the renderer's font. `topbar.js` loads right after
  the `<header>` — in the `<head>` it found no `#appTopbar` and the bar stayed empty.
- **Text options (2026-10-21)**, per text field and fixed text: `prefix` (a fixed label before the value, e.g.
  "CP:", hidden with the field by `hide_if_empty`) + `prefix_bold`, `invert` (white on a black band), `frame` (its
  own box) and `rotate` (90 = reads downward, 270 = upward; the text starts at `top`, its length runs to the
  bottom edge and `width` is then % of the HEIGHT; `align` is ignored). PDF: `_draw_text_entry` (horizontal
  still draws in page coordinates, vertical rotates the canvas); band = from 0.3×size below the baseline to the
  line's top, padded 0.15×size (`TEXT_BAND_*`), the field's whole box horizontally (clamped to the label) and the
  text's length vertically. ZPL: one field with prefix+value, `^A0R`/`^A0B` (270 right-justified so it hugs the
  top), band/frame as `^GB` and the inverted text with `^FR`; **two approximations**: inverted text isn't faked
  bold (a second `^FR` pass would turn it black again), and `prefix_bold` alone is only reprinted when the text
  starts at the box start (left / 90°). Editor: `text-inverted`/`text-framed`/`text-rot-*` classes and the
  prefix as a `::before` (so `collectFields`, which reads `textContent`, never saves it into `text`). Tests:
  `apps/labels/tests/test_text_options.py`.
- **Shapes, boxes, color and layers (2026-10-28, phase 1 of
  `docs/superpowers/specs/2026-10-28-editor-rotulos-galeria-design.md`; plan
  `docs/superpowers/plans/2026-10-28-editor-fase1-formas-color.md`).** Two optional `design` keys: `palette`
  (`{primary, accent}`, `#rrggbb`) and `shapes` (max 60): `{id, kind: rect|ellipse|line, left, top, width, height`
  (% of the label; a line is horizontal with `height` 0 or vertical with `width` 0), `fill`, `stroke` (hex,
  `"primary"`, `"accent"` or null), `stroke_width` (pt, default 0.6), `stroke_style` (solid|dashed 4/3 pt|dotted 1/2
  pt), `radius` (mm, rect), `z`, `locked`, `text` (a box with content = a cell: `{value` with `{{markers}}`,
  `font_size, bold, align, valign, color, padding` mm default 1, `hide_if_empty}`)`}`. Validation in
  `design_shapes.py` (also `resolve_color`, `thermal_tone`); drawing in `shapes_pdf.py` (called by `draw_label_page`
  right after the decorations) and `shapes_zpl.py` (same place in `render_label_zpl`), in `z` order. **Shapes are the
  background layer**: over the border/lines, under logo, codes and texts (the editor mirrors it: decoration layer <
  shape layer < `.field` z2 < fixed texts in their own layer z3). Rules target a shape as `"shape:<id>"`
  (`apply_design_rules` copies and indexes them). **Thermal**: luminance < 96 → black, > 245 → nothing, in between a
  Bayer 4×4 screen (245, not 200, so a light OCA-style header gray still prints); strokes/lines print black unless
  white; light text (≥ 160) goes with `^FR`. Black fills use `^GB` (5th param = corner rounding) / `^GE`; gray fills
  and dashed strokes on rounded/ellipse shapes are rasterized with Pillow to `^GF` with ZPL ASCII compression
  (`compress_rows`), so a full-label gray at 300 dpi stays small. A design without `shapes`/`palette` prints
  byte-identical (pinned by `test_existing_standard_template_unchanged` in `test_shapes_pdf.py`/`test_shapes_zpl.py`,
  hashes taken before the change). Editor: `assets/js/label_editor_shapes.js` (`window.LabelEditorShapes`): panel
  "Formas y colores" (add rect/circle/box/lines, the two palette colors, thermal-color notice) and "Forma
  seleccionada" (position/size in %, fill/stroke swatches with P/A palette refs, stroke width/style, corners, text,
  front/back, lock, delete); drag, resize handle, arrows, Delete; the snap module uses shape edges.
- **Typography and icons (2026-10-28, phase 2 of the same spec; plan
  `docs/superpowers/plans/2026-10-28-editor-fase2-tipografia-iconos.md`).** New optional keys on every text — fields,
  `texts[]` and a shape's inner text: `font_family` (sans|serif|script|condensed|mono, default sans), `italic`,
  `uppercase`, `letter_spacing` (pt, -1..10), `max_lines` (1..10, default 1) and `line_height` (0.8..3, default 1.15,
  only with several lines); fields and fixed texts also take `color` (hex/`primary`/`accent`; a shape's text already had
  it). Validation in `text_style.validate_text_typography`; `text_style.py` is the one place for family → font file/PDF
  name, case, letter-spaced width, ellipsis fitting and wrapping (a missing variant falls back: script and condensed
  have no italic). Fonts (OFL, no CDN) live in `backend/apps/labels/fonts/` with an IDENTICAL copy in
  `frontend/assets/fonts/` of the four OFL families (Liberation Sans is backend-only) (`test_frontend_copies_are_identical`): **serif is Crimson Text** (Libre Baskerville only
  ships as a variable font now), script Kalam, condensed PT Sans Narrow, mono Courier Prime; sans is the built-in
  Helvetica in the PDF and **Liberation Sans only to draw sans as a graphic in ZPL**. PDF: `max_lines` > 1 wraps by
  words and the last line ends with "…"; one line keeps the old truncation; `shrink_to_fit` is ignored when `max_lines` > 1 (PDF and ZPL). ZPL: **native `^A0` only for sans without
  italic or spacing** (several lines go through `^FB`, so the PRINTER wraps and may break at a different word than the
  PDF; what doesn't fit is cut with "…" BEFORE sending, using a Helvetica wrap — the Zebra overprints the overflow
  on the last line, unreadable, seen in Labelary; font 0 is narrower, so the cut text fits); anything else is rasterized with Pillow (`zpl_text_image` → `^GF`, `^FR` when inverted or light). The invert/frame band follows the REAL line count (graphic: the wrapped lines; native: a Helvetica estimate). Rotated
  non-native text stays `^A0` (approximation). Icons are one more shape, `kind: "icon"` + `icon` (fragil, arriba,
  no_mojar, no_apilar, telefono, casa, corazon, estrella, camion, caja) with the color in `fill`: geometry in
  `backend/apps/labels/icons.json` (24×24 grid of `poly`/`circle` primitives; identical copy
  `frontend/assets/data/label_icons.json`, `test_icons_json_copies_identical`), drawn by `icons.py` in the largest
  square that fits the box, centred (`icon_box`, never stretched): vector in the PDF, `^GF` from Pillow in ZPL (black,
  white only if white, like borders). Editor: `assets/js/label_editor_type.js` (`window.LabelEditorType`: the shared
  "Tipografía" block and `applyToElement`, which mirrors the PDF with `@font-face` families `RotulosSerif`, `RotulosScript`...
  in `editor_rotulos.html`) and the "+ Ícono" button with its icon grid in `label_editor_shapes.js`. The canvas shows
  `{{markers}}` raw unless the "Ver con datos de ejemplo" toggle is on (phase 3, below); a wrapped name is exact only in "Ver cómo sale impreso".
- **Images and new data (2026-10-28, phase 3 of the same spec; plan
  `docs/superpowers/plans/2026-10-28-editor-fase3-imagenes-datos.md`).** The business's own image library:
  `LabelImage` (`label_images.py`, migration `labels/0013`), API `/api/v1/labels/images/` (list/create/destroy, plus
  `?ids=a,b[&template=<id>]` to fetch specific ones — only `[0-9]{1,18}` parts count, at most 200 ids, `template` adds the
  images of that readable design), per business (`data_owner`). **GET needs `labels.template_create` OR `labels.edit`**
  (whoever edits saved labels must see the images their designs use); POST `labels.template_create`, DELETE
  `labels.template_delete`. `OWNED_DATA` includes `labels.LabelImage` (a business with only images isn't "empty"). Upload limits 4 MB, 40 MP, 300 images per business;
  `normalize_upload` applies EXIF orientation, strips EXIF, caps the long side at 2000 px, keeps PNG for
  transparent/PNG/GIF sources (a flat logo gets no JPEG artefacts) and JPEG q90 for the rest, random file name;
  delete = soft (`is_active=False`: what already uses it keeps printing); files are public in Caddy
  (`/media/labels/images/*`, random names). Shape keys on `kind: "image"`: `image` (`"logo"` or a LabelImage id),
  `fit` (contain|cover), `circle`, plus the usual `radius` (rounded corners), `stroke`/`stroke_width` (border), `z`,
  `locked`; no `text` (validation in `design_shapes.py`, `image_ids(design)`). **`allowed_image_ids(user,
  extra_designs)`** = own images (active or not) ∪ ids used by public/system templates ∪ ids in the design being
  edited; it is the one rule on save (serializer), on `preview/` and on `images/?ids=`, so a template can't point at
  another business's file. **`"logo"` = the sender logo** (`sender_logo(owner)` / the store's), and preview and
  thumbnails fall back to `sender_logo` when no logo is passed. A missing file or id draws nothing and adds the
  warning `image_missing` (`X-Label-Warnings`, shown in the preview dialog). PDF: `label_images.prepare` resamples to
  the box at 300 dpi capped at 2400 px (`IMAGE_DPI`/`IMAGE_MAX_SIDE_PX`, `shapes_pdf.py`) with cover/contain, circle
  and rounded masks, drawn in `z` order among the shapes. ZPL: the same `prepare` result flattened on white and
  dithered to a 1-bit `^GF` (`shapes_zpl.py`), so photos print as halftone. **Codes over shapes get a white box in ZPL too**: when any shape's box overlaps a QR/barcode box plus `QUIET_ZONE_CM`, `zpl._white_box_commands` emits a solid white `^GB` (quiet zone per side, like the PDF's `_draw_white_box`) right before the code; designs without shapes stay byte-identical. **New markers** (`label_editor_markers.js`
  + `build_label_context`/`build_shipment_context`/`SAMPLE_LABEL_CONTEXT`): `{{nombre}}` = first word of the ADDRESS
  recipient only (initial uppercased, rest as typed; titles are kept: "Dra. Ana" → "Dra."), empty without one — never the business name; `{{peso}}` =
  `total_weight_kg` as "2,5 kg" (comma, no trailing zeros, empty if none); `{{bultos_pedido}}` = `Order.package_count`,
  display only (a 3-package order still prints ONE label; `{{bulto}}`/`{{bultos}}` stay the per-label numbering);
  `{{transportista}}` = `Order.carrier`. Same rule as always: no products, prices or buyer contact. `GET
  /api/v1/labels/preview-context/` (`PreviewContextView`, `labels.render`) returns the preview's sample data
  (`context`) plus the resolved computed variables (`computed`: fecha, hora, secuencia, bulto, bultos...), so the
  canvas shows the same text as the PDF. Editor: `assets/js/label_editor_images.js` ("+ Imagen" with the picker/upload
  dialog, fit/circle/corner controls) and the **"Ver con datos de ejemplo" toggle**, which only paints samples on the
  canvas: the field's real text (the `{{markers}}`) stays in `data-raw` and `withoutSamples(fn, rerender)` removes the
  samples around every save/thumbnail/collect, so a sample is never stored in the design.
- **Editing comforts (2026-10-29, phase 4; plan `docs/superpowers/plans/2026-10-29-editor-fase4-comodidades.md`).** A
  canvas toolbar above the label. **Undo/redo** (`assets/js/label_editor_history.js`, `window.LabelEditorHistory`): snapshots
  of the whole design (`collectFields()` + size, max 100), recorded only when the snapshot changed, at the end of each gesture
  (document `pointerup`, `change`, `input` debounced 500 ms, arrow/Delete `keyup`); restore = size + `applyFields` + code
  previews; reset after the rótulo/template loads. Shortcuts: Ctrl/⌘+Z, Ctrl/⌘+Shift+Z and Ctrl/⌘+Y, Ctrl/⌘+D duplicates
  the chosen shape, line or fixed text (copy +2 %, unlocked; data fields are unique), Supr deletes a chosen line/fixed text
  (shapes already did) — **none is intercepted while typing** (`LabelEditorHistory.isTypingTarget`: text inputs, textarea,
  contenteditable keep the browser's own text undo; a checkbox/select/color doesn't count, so Ctrl+Z after ticking one undoes
  the design) nor with the preview dialog open. A text field dragged is blurred on release (it is always contenteditable, and
  with the caret left in it Ctrl+Z undid its text instead of the drag). **Multi-select** (`assets/js/label_editor_align.js`): Shift+click toggles fields, fixed texts, lines and shapes
  (decorations carry `data-deco-index`); with 2+ the bar shows align left/centre/right/top/middle/bottom and, with 3+, even
  distribution; boxes come from the DOM in % so every type aligns alike (a text field counts its visible text, measured with a
  Range — its box runs to the right edge), and each type moves through its own adapter; a locked element doesn't move and is
  the reference. Esc or a plain click clears. **Zoom** 50-300 % (buttons, Ctrl+wheel over the canvas, per browser in
  localStorage) only scales the label's CSS size — everything measures with `getBoundingClientRect` and stores %, so saved
  positions don't change; the html2canvas export/thumbnail divide their scale by the zoom so their resolution doesn't. **Thermal view**: `POST /labels/preview/` with `"thermal": true` returns the same
  render as a 1-bit PNG at 203 dpi (`thermal_preview.py`: the PDF rasterized with pypdfium2, black below luminance 96, nothing
  above 245, the Bayer 4×4 screen in between — exactly the shapes' ZPL rule, ink where `bayer < tone*16`); an approximation
  (the Zebra draws the sans with its own font and prints grey strokes/lines/icons solid black), said in the dialog. Not in
  the history: an unfinished "mover" rule (`LabelEditorRules.collect` drops it) disappears on undo. Tests: `tests/test_thermal_preview.py`.
- **Gallery (2026-10-29, phase 5; plan `docs/superpowers/plans/2026-10-29-editor-fase5-galeria.md`).** "+ Nueva plantilla"
  (Mis plantillas) opens `galeria_plantillas.html` + `assets/js/galeria_plantillas.js`: "En blanco" plus the system
  templates with `LabelTemplate.gallery_order` (`GET /labels/templates/?gallery=1`, ordered; the field is read-only in the
  API), each card linking to `editor_rotulos.html?nueva=plantilla&template=<id>` — the editor loads it as a starting point
  and "Guardar plantilla" creates the business's OWN copy; the system one is untouched (in that mode `loadFromTemplate`
  does not offer "Guardar cambios en «…»": an admin can edit system templates and would have changed it for everyone). The seven new designs (H Moderno, D
  Con la marca, C Destacado, E Editorial, F Banda lateral, G Postal, B Celdas; A, the standard, is 8th and unchanged) live
  as data in `backend/apps/labels/gallery.py`, built ONLY with keys the editor edits (the user's rule: the editor must be able
  to build every gallery design); brand images are the `"logo"` image shape (no stock images). Migration `labels/0015` seeds
  them as `owner=None`, public, with their v1 — a COPY of the designs (`test_migration_copy_matches_gallery_module` keeps it
  equal to `gallery.py` today; a later change to a design needs a new migration); idempotent by name. Thumbnails come from
  `generate_template_thumbnails`. `tests/test_gallery.py`: order, validation, only label markers, every data field placed, PDF
  and ZPL (8/12 dpmm) without code warnings, QR + Code128 read at 203 dpi. **A design without a `logo` key now opens without
  the old logo field** (`applyFields` hides `#logoField`, `collectFields` skips it; "Subir logo" shows it again): saving
  used to add it at its default spot and the gallery's logo printed twice. **Known, not fixed:** ZPL encodes Code128 as
  subset B (`^BCN,...,N,N`), so a 19-digit tracking prints ~6 cm wide in a Zebra vs ~4.4 cm in the PDF and can spill out of
  its box — "Celdas" gives the barcode a full-width row for that; switching to `^BC` mode A changes every existing ZPL (the
  pinned hash tests), so it is a separate decision.
- The **field properties panel** (`fieldPropsGroup`) edits those per-field styles: click a text field on
  the canvas and set `font_size`, `bold`, `align`, `width`, `hide_if_empty`, `shrink_to_fit`. It follows
  the same select-then-configure pattern as the QR/barcode panel beside it. An empty or unchecked control
  **deletes** the key instead of storing a falsy value, so the design keeps only what the user actually
  chose and the renderer's defaults (proportional size, full width to the right edge) stay in force.
- `collectFields()` re-emits the styles, the decorations, `rules` and `locked`. Before, opening a template and
  saving it silently stripped its border, rules and fixed texts. It also flattens each text to one line: the
  default fields carry the HTML's line breaks and indentation, and the PDF drew them as "■".
- **Pieces of the editor in their own files** (the page is already large): `label_editor_markers.js` (the
  `{{...}}` catalogue — shipment data from `build_label_context` + the computed ones from
  `build_computed_context`; if one is added there, add it here —, "Insertar dato" in the field, fixed-text and
  code panels, and the `{{secuencia}}` numbering section), `label_editor_rules.js` (the "Reglas según los datos
  del envío" section: one row per rule, reorderable because the last one touching an element wins; a "mover"
  without both coordinates is dropped on save, with a confirm first) and `label_editor_snap.js` (snapping to the
  other fields' edges, the centre and the separator lines, with guides; Alt turns it off; texts only offer their
  left/top edge because their box runs to the right edge).
- **Locking**: `design.locked` is a list of field names (lines and fixed texts carry `locked: true` instead). A
  locked element is still selected (to unlock it or edit its text) but not dragged nor moved with the arrows
  (0.5 %, Shift 5 %; Alt+arrows while typing in a text). Editor-only: the PDF and the ZPL ignore it, and a rule
  can't target it (`serializers.EDITOR_ONLY_KEYS`). **Layer order exists only among shapes** (their `z`, see
  "Shapes, boxes, color and layers" below): the PDF draws decorations, shapes, logo, codes and then texts, so text is
  always on top of any shape.
- `GET/PUT /labels/sequence/` (`LabelSequenceView`, `labels.edit`): `{prefix, padding, next}` of the user's
  `LabelSequence` key `default`, the only one the render uses; `next` is stored as `current = next - 1`, so it's
  the same counter `next_value` increments. Audited as `label.update`.
- Fixed while adding the above: `.tool-group { display: flex }` beat the `hidden` attribute, so every
  select-to-show panel (QR, field, decoration) was always visible; and `selectDeco`/`refreshDecoPanel` live in the
  module but the classic script calls them — module functions aren't global, so clicking a line or fixed text threw
  and decorations couldn't be dragged (now exposed on `window`). A text field's box reaches the right edge, so the
  drag clamp never let a text move right (`maxLeftFor`).
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

**Template versions (`template_versions.py`, `LabelTemplateVersion`, T#254-256).** A version is what a template
draws — `name`, `width_cm`, `height_cm`, `design` — plus an optional `note`, `created_by` and `restored_from`. v1
on create (migration `labels/0011` gave every existing template its v1), a new one on any update that changes
those four (`record_version` compares with the last one: making it public or editing the description leaves no
empty version), and the copy of a duplicate starts its own history ("Copia de «X» (versión N)"). The number is
taken with the template row locked. **Restoring never rewrites history**: `POST templates/<id>/versions/<n>/restore/`
copies v n onto the template and records it as a NEW version with `restored_from` (same permission as editing,
`_check_can_edit`; audited `template.update`). Also `GET .../versions/` (full history with designs, for the
comparison) and `GET .../versions/<n>/preview/` (that version's sample PDF). The template serializer exposes
`current_version` (annotated in `get_queryset`) and `can_edit` — the frontend reads that instead of copying the
rule — and takes a write-only `version_note`. Frontend: the editor opened with `?template=<id>` on an editable
template shows "Guardar cambios en «X»" (asks for the note, `updateTemplate`); before this a template's design
could never be changed after creating it. `mis_plantillas.html` cards link "Versiones (N)" to
`versiones_plantilla.html` + `assets/js/versiones_plantilla.js`: history, "Ver cómo sale", "Restaurar", and an
A/B comparison computed client-side (`diffVersions`: name, size, each design key added/removed/changed and
which properties).

**Template thumbnails (`thumbnails.py`).** `LabelTemplate.preview` is the PNG each card of
`mis_plantillas.html` shows. It existed but nothing filled it, so every card read "Ver vista previa". Now
`LabelTemplateViewSet` regenerates it on create, on an update that touches `design`/`width_cm`/`height_cm` (or
when there is none) and on duplicate, unless the request sends its own `preview`. It is the SAME PDF as
`templates/<id>/preview/` (`SAMPLE_LABEL_CONTEXT`, `is_preview=True` so `{{secuencia}}` isn't consumed)
rasterized with **pypdfium2** (ships its own binary, no system packages) at `THUMBNAIL_WIDTH_PX` (480), so it
can't disagree with what prints. A design that can't be drawn is logged and the template saves anyway, with no
thumbnail; `generate_template_thumbnails` backfills.

**Scanning tests (`tests/test_scanning.py`, T#241/T#296).** A simulated reader: the real label is rasterized
with pypdfium2 at 203 dpi in pure black and white (what a Zebra prints) and decoded with zxing-cpp, for both
systems (template PDF and element-layout PNG): QR, Code128, EAN-13, a long carrier URL, codes shrunk by
`_fit_size_cm`, the seeded standard template with a real order.

**Barcodes are drawn in whole 203-dpi dots (`build_barcode_drawing`, `_barcode_module_cm`).** Stretched to an
arbitrary width, a module fell on 1.4-1.8 printer dots and the printer rounded some bars one way and some the
other: a 19-digit Code128 (OCA) didn't read below ~5 cm. Now the module is the largest WHOLE number of
`THERMAL_DOT_CM` (2.54/203) that fits the box, minimum `MIN_BARCODE_MODULE_DOTS` (2 = 0.25 mm; 1 doesn't read
even at 300 dpi), with Code128's quiet zone at 10 modules instead of ReportLab's fixed 0.25" per side. Every
bar then rounds the same way wherever the code starts; it also reads at 300 dpi. The bars are **centred in
their box** (the box the editor draws — its `<img>` is `object-fit: contain`, so the editor mirrors this with
no change). `barcode_min_width_cm(data)` is the readable minimum (~4.4 cm for 19 digits). A box narrower than
that draws the code at the minimum anyway, wider than its box up to the label's edge (warning
`barcode_widened`, same rule as ZPL); with no room even to the edge it is squeezed and logged
(`barcode_unreadable`). `render_label_pdf`/`draw_label_page` take an optional `warnings` list;
`PreviewLabelView` returns them as `X-Label-Warnings: code:min_cm,...` (CORS-exposed) and the editor shows
them in the "Así sale impreso" dialog. The preview sample tracking is 19 digits so it hits the worst case.
ZPL's `^BY` module also starts at 2 dots now (`BARCODE_MODULE_RANGE`). Pinned by
`test_minimum_readable_barcode_width_at_203_dpi` (a 4 cm box now reads, widened) and
`test_a_barcode_box_narrower_than_the_old_limit_reads_inside_its_box`.

**Large batches in the background (`batch_jobs.py`, T#298).** Up to `LABELS_BATCH_SYNC_MAX_ITEMS` (25) a batch is
drawn inside the request as before; above it `LabelBatchView` stores what it needs in `Document.job` (ids in
order, template, output, layout, dpmm, printer) and answers 202 with the document "processing" and
`queued: true`. The integrations worker calls `run_pending_batches()` every loop and draws it with the SAME
`_run_batch`; the table of documents is the queue (no Celery/Redis). Claimed with a conditional UPDATE on
`updated_at`; a claim older than `LABELS_BATCH_JOB_TIMEOUT_SECONDS` is retried up to
`LABELS_BATCH_JOB_MAX_ATTEMPTS`, then "failed" with the reason; an order deleted meanwhile is skipped and
noted. With a printer, the print job is created when the document is ready. `LABELS_BATCH_MAX_ITEMS` is now
1000. Front: `documentos.html` already polls "processing"; `?generating=1` explains it, and to a printer the
merchant lands on the queue.

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

### Direct printing (`apps/printing`, T#322-T#328)

Printing straight to the merchant's printers instead of downloading the file. Our server can't reach a
printer in the merchant's network, so a small **agent** runs on one of their PCs
(`apps/printing/agent/rotulos_print_agent.py`: one file, Python 3.8+, stdlib only, downloaded from
`impresoras.html`), polls the API with its token, sends each job to the printer and reports back.
- Models: `PrintAgent` (token `rpa_<prefix>_<secret>` shown ONCE, stored as SHA-256 — random and long, so no
  slow hash; revoking = `is_active=False` and its printers lose their agent), `Printer` (`format` pdf|zpl,
  `dpmm` for zpl, `location`, `connection` network → `host`/`port` 9100 | system → `queue`, its `agent`, one
  `is_default` per owner, soft-deleted) and `PrintJob` (pending → printing → printed|error, or cancelled;
  `attempts`, `error_message`).
- **A job is an already generated `Document`** sent to a printer, so template, store rules, density and the
  `label.batch` audit are the batch's own: `POST /labels/batch/` with `printer_id` takes `output`/`dpmm` from
  the printer and answers `print_job` (or `print_error`). `POST /printing/jobs/ {document_id, printer_id}`
  queues an existing document; the format must match the printer's.
- Merchant API under `/api/v1/printing/` (`labels.batch`, own rows only): `agents/` (create returns
  `token`), `printers/`, `jobs/` (`?status=pending,error`, `?printer=`) + `retry/` (error|cancelled →
  pending) and `cancel/` (pending|error), `agent/download/`. Agent/printer changes audited as
  `printer.update`.
- Agent API (`agent_views.py`, `Authorization: Agent <token>`, no session, throttle scope `print_agent`):
  `GET agent/jobs/next/` (claims the oldest pending job of ITS printers with a conditional UPDATE, so two
  agents can't take the same one; 204 when none; every call marks the agent online and stores
  `X-Agent-Version`), `GET agent/jobs/<id>/file/`, `POST agent/jobs/<id>/result/ {ok, error}`.
- **A claimed job the agent never confirms is NOT reprinted**: after `PRINT_JOB_CONFIRM_TIMEOUT_SECONDS`
  (300) it becomes `error` with a "check the printer before retrying" message — the agent may have died
  after printing, and duplicated labels are worse than a notice. "Online" = polled within
  `PRINT_AGENT_ONLINE_SECONDS` (60).
- The agent prints "network" printers by opening a socket to host:port (works the same on any OS) and
  "system" ones with `lp` (`-o raw` for ZPL) on Linux/Mac, `copy /b` to the share for ZPL on Windows and
  SumatraPDF for PDF on Windows; `ROTULOS_PRINT_COMMAND` (`{file}`, `{printer}`) overrides all of it.
  **Windows is A CONFIRMAR** (written, not tried on a real Windows PC). **The .exe (2026-10-22)**: the
  backend pipeline's `AgentWindows` stage builds `RotulosAgente.exe` with PyInstaller on `windows-latest` and the
  `Image` stage drops it in `apps/printing/agent/dist/` before `docker build`, so the API serves it at
  `agent/download/?platform=windows` (`PRINT_AGENT_WINDOWS_EXE`; 404 with a "use the Python one" message when the
  server doesn't have it, e.g. locally). For double click the agent (1.1.0) reads `rotulos_agent.json`
  (`{server, token}`, downloaded from `impresoras.html` right when the token is shown) next to the program or in
  the user config folder, asks and saves it when missing, offers to start with Windows (`--install-startup` /
  `--remove-startup`: a `.bat` in the Startup folder) and waits for Enter before closing on an error.
  Packaging was tried on Linux only (the frozen binary reads the json beside it).
- Frontend: `impresoras.html` + `assets/js/impresoras.js` (agents with the one-time token and the command to
  run, printers CRUD), `cola_impresion.html` + `assets/js/cola_impresion.js` (menu `print_queue`; refreshes
  every 5 s while something is pending/printing, 20 s otherwise; warns when no agent is online), and in
  `imprimir_rotulos.html` the printers are one more group of the same "Formato" select (default printer
  preselected); sending lands on the queue. Shared CSS `assets/css/impresion.css`.
- Tests: `apps/printing/tests/` — `test_agent_program.py` runs the real agent against a live server and a
  socket standing in for a Zebra on port 9100.

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
  all orders share one) or `dispatch` (any carrier, since 2026-10-14). Shared in `store_print.py`: `parse_action`,
  `action_link(connection, orders, action, route)` → `(url, summary)` and `render_document(token, platform)`
  (what `_print_document` serves; the token carries `a` and, for dispatch, the shipment ids `s`).
  **Dispatching happens in the POST, never in the GET of the link** (it creates carrier shipments); the
  link only prints them (`apps.carriers.printing.bundle`: rótulo + carrier label). `apps.carriers.store_dispatch`
  picks **the carrier** by itself — the store's `preferences["dispatch_carrier"]` (chosen in `tiendas.html`,
  "Despachar desde la tienda con"; a chosen carrier whose account isn't ready is an error, never a silent
  fallback), else the owner's only ready carrier, else the only one this store offers in its checkout, else
  400 "elegí con cuál…" — and **the contract** — that carrier's checkout contract if it's a home one, else
  the account's first home one (branch contracts need a branch per order: only from Mis pedidos) —, reuses
  an order's open shipment (any carrier) instead of creating another, and returns `failed: [{number,
  detail}]`; no account at all → 400 "Conectá tu cuenta de Andreani, Correo Argentino u OCA…". Max
  `MAX_ORDERS_PER_DISPATCH` (30) per action. The store-facing texts say "Despachar con tu transportista"
  (Woo plugin 1.4.0, Shopify `rotulos-perso-8`, the Tiendanube link renamed in the Partner Portal).
  Cambiar estado / Exportar were left out on purpose: every store already has them and the store owns the
  status. Magento has them through our module (phase 2, below); not available in VTEX (no admin module of ours) nor Empretienda (no API). Per platform:
  WooCommerce plugin 1.4.0 (three bulk actions, dispatch with a 90 s timeout, failures shown as an admin
  notice); Tiendanube = one Partner Portal link per action, each to its page (`imprimir_tiendanube.html`,
  `planilla_tiendanube.html`, `despachar_tiendanube.html`, same JS with `<body data-action>`; the user
  must register the two new links); Shopify = a document choice ("Rótulos" / "Planilla de retiro") INSIDE the
  one print action — **Shopify allows a single print-action extension per app**: a second one
  (`rotulos-planilla`) made `shopify app deploy` fail, 2026-10-14 — and an admin action
  (`rotulos-despachar`, target `admin.order-index.selection-action.render`, confirms with a button) in
  `rotulos-extension/`, released as version `rotulos-perso-7` (2026-10-14). Tests:
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
- **Requests to a URL the merchant typed (WooCommerce, Magento) go through `safe_http.py`** (2026-10-20
  security review): the host is resolved and any non-public address (private, loopback, link-local, CGNAT,
  reserved) is refused with `ProviderRejectedError`, also on every redirect, which is followed by hand (max 3;
  credentials dropped when the host changes; 301/302/303 → GET). Without it `https://10.0.0.5` or a domain
  resolving to 127.0.0.1 made our server call its own network (SSRF). `INTEGRATIONS_ALLOW_PRIVATE_HOSTS`
  (default = `DEBUG`) turns it off for the local test store, which resolves to a Tailscale 100.x address.
  VTEX/Shopify/Tiendanube hosts are ours or a fixed suffix, so they don't need it.
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
    another system (the merchant's ERP using the same key) is never overwritten** (one carrying OUR secret in `key` is ours even on another domain, e.g. after changing domain, and is re-pointed): `last_error` asks for a
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
  - **Phase 2 — our module "Rotulos_Envio" (`magento_module/`, `admin_module.py`, 2026-10-22, written WITHOUT a
    store, A CONFIRMAR)**, like the WooCommerce plugin and optional (without it everything still works by
    reconciliation): an observer on `sales_order_save_commit_after` POSTs `{event: order_saved, order_id}` to the
    generic `magento/webhooks/?store=<id>` (`order_sync_events = ("rotulos/order_saved",)`,
    `webhook_secret_per_store`; only the id is queued, the worker refetches); three mass actions in Sales → Orders
    (labels / manifest / dispatch → `magento/print-link/` → redirect to `magento/print/<token>`); a carrier
    (`carriers/rotulos`) that quotes at checkout through `magento/rates/` (Woo's `rates.quote`, reused as is; 5 s
    timeout, answers cached 5 min). Everything is signed like the Woo plugin — the verification moved to
    `store_print.verify_signed_request` (Woo's `verify_plugin_request` now wraps it). Nothing to configure by hand:
    `configure_admin_print` writes store/secret/URLs through the module's own REST route `POST /V1/rotulos/config`
    (ACL `Rotulos_Envio::config`, which the merchant ticks in their Integration and reauthorizes); 404 = module not
    installed and 401/403 = permission missing, both leave `print_plugin` false without touching the store.
    `webhooks_required = False` (new `StoreProvider` flag): without `INTEGRATIONS_PUBLIC_BASE_URL` a Magento store
    is not marked with the missing-URL error. Zip at `GET magento/module/` (unpacks as `app/code/Rotulos/Envio`),
    shown on each Magento store in `tiendas.html` (`renderMagentoModule`). PHP checked with `php -l` only.
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
  tiendanube/install-share-link/` (`stores.manage`) returns a public URL `.../tiendanube/install/<token>/`
  signed with the user (`make_share_token`, `INTEGRATIONS_INSTALL_SHARE_MAX_AGE_SECONDS`, 72 h); whoever
  opens it, with no account of ours, gets a FRESH short-lived `state` for that user marked `x=1`
  (`make_oauth_state(..., shared=True)`), and the callback (`oauth_state_is_shared`) sends them to the
  public `tienda_conectada.html` (`STORE_SHARED_CONNECT_FRONTEND_PATH`) instead of `tiendas.html`. Built
  from `INTEGRATIONS_PUBLIC_BASE_URL` (503 without it). The button lives in `tiendas.html`'s Tiendanube row. already-owned stores redirect with `store_connected=<id>`, failures with
  `store_error=<code>`. `POST stores/<id>/disconnect/` revokes (never deletes). `PATCH stores/<id>/settings/` saves how that store's
  labels print — `sender_name`/`sender_address`/`sender_phone`, `logo` (file or the editor's base64 data
  URL, 2 MB cap), `default_template` (must be public or the caller's), `label_printer_dpmm` (its
  thermal printer's density, a column rather than a `preferences` key because the merchant edits it and
  it has to be validated; `null` = not configured) and `dispatch_carrier` (a carrier code or "", kept in
  `preferences` and written with an UPDATE AFTER `serializer.save()`, which saves the whole row and would
  overwrite it) — never token/status; audited as `store.update` (the
  logo logs its filename, not the bytes). Edited per store in `tiendas.html`.
  `batch_views` uses them when printing: the store's logo goes on its orders' labels, and with no
  `template_id` the batch falls back to the store's `default_template` when every order shares it, else
  to the oldest public template. The system template "Etiqueta de envío estándar" prints
  `remitente_domicilio`/`remitente_telefono` with `hide_if_empty`, so a store without them looks exactly
  as before (migration `labels/0010`). All gated by `stores.manage` (reading the store list: `orders.view`).
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
- **The merchant's table.** `/api/v1/integrations/shipping-rates/` (`carriers.manage`, full CRUD, `?store=`)
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
- **The merchant's view.** `GET /api/v1/integrations/store-labels/` (`orders.view`, read-only, filters
  `?store=` and `?status=`) lists the caller's own store label requests with the failure reason — never
  the stored `payload` (buyer data) nor the download token. `rotulos_tienda.html` +
  `assets/js/rotulos_tienda.js` render it, linked from `tiendas.html` and the `store_labels` menu item.

- Privacy webhooks (`privacy.py`, work for revoked stores too): `customers/redact` anonymizes the matched
  orders, `store/redact` revokes the store and anonymizes everything, `customers/data_request` emails a
  JSON report to the store owner (no owner → fails without retry, report stays in `event.result`). Orders
  are anonymized, never deleted.

### Carriers (`apps/carriers`) — common layer, Andreani, Correo Argentino, OCA

**Common layer** (added 2026-10-14, user's choice over copying the Andreani model): like `StoreProvider` for
stores, each carrier is a `CarrierProvider` (`base.py`) in its own folder, registered in `providers.py`
(`get_provider`/`all_providers`). The provider declares what its account needs (`account_fields`: each
`AccountField` stored in a `CarrierAccount` column, the encrypted `password`, the encrypted JSON
`secrets_encrypted` via `secret()`/`set_secret()`, or `extra`; `required_for="quote"` = only needed to
quote), its "contract" vocabulary (`contract_label`, `contract_one`, `default_contracts`,
`contracts_from_test`), `uses_volume`/`uses_dimensions` (`default_length/width/height_cm`), and
implements the remote calls (`quote`, `submit` → `CreatedShipment`, `label`, `fetch_events` →
`CarrierEvent`s already mapped to shipment/order status, `cancel_remote`, `branches(account, cp, state)`,
`test_credentials`). The orchestration is inherited: `validate_new_shipment`, `create_shipment` (quote
best-effort, then `apply_shipping` → order PREPARING with `carrier`/tracking/URL), `apply_events`
(forward-only, returns/cancellation override), `cancel_shipment`, `due_shipments`. Andreani predates it:
`andreani/provider.py` wraps its existing `shipments.py` and keeps its own routes.
- **Packages per order** (2026-10-22): `shipments/` and `shipments/quote/` also take `packages: {order_id: n}` (1-20) over the shared `package_count`; the Despachar dialog has one "Bultos" box per order (starting at the order's `package_count`) plus "Bultos (para todos)".
- **Common API** (`views.py`; `orders.dispatch`, account/checkout/push writes `carriers.manage`, reads of the list and shipments `orders.view`): `GET carriers/` (catalog + the user's account state per
  carrier: `ready`, `missing`, `quote_missing`), `GET/PUT accounts/<carrier>/` (+ `carrier_info`),
  `POST accounts/<carrier>/test/`, `GET branches/<carrier>/?cp=&state=`, `GET/POST shipments/` (POST
  takes `carrier`), `shipments/quote/`, `shipments/print/` (`printing.bundle`: our rótulo + each
  shipment's carrier label, any mix of carriers), `shipments/<id>/`, `.../label/`, `.../refresh/`,
  `.../cancel/`. **The carrier goes AFTER a prefix** (`accounts/andreani/`) on purpose: `andreani/account/`
  is Andreani's original view (registered first), and a common `<carrier>/account/` collided with it.
- `tracking.sync_due_shipments` loops every provider; a `CarrierAuthError` deactivates only that account.
  Provider errors subclass `CarrierError`/`CarrierAuthError`/`CarrierRejectedError` (Andreani's too).
- `common.py`: `postal_code_digits`, `province_code` (name/alias/CPA letter → ISO 3166-2:AR letter, what
  Correo needs), `split_packages`, `box_cm`. `CarrierAccount.origin_state` is the origin province.
- **Novedades push** (`push_views.py`, `CarrierPushToken`): `GET/POST accounts/<carrier>/push/` generates
  ONE public URL per account (`push/<carrier>/<key>/`) + Basic credentials (password hashed, shown once).
  Only providers with `supports_push` (Andreani). The receiver applies each novelty to the shipment of
  THAT account with that number (`receive_push`; Andreani's maps `{NumeroDeEnvio, Estado, FechaEstado,
  Motivo, SubMotivo, Ciclo}` to a trace and reuses `shipments.apply_traces`), answers
  `{"status": "OK"|"ERROR", "message", "error"}` with 200 (Andreani only retries 5xx), 401 on bad
  credentials. Andreani's push is a PAID service the client asks their sales rep for (doc v1.6).
- **OCA (`oca/`)** — e-Pak web services, HTTP POST form → .NET DataSet XML (`client.dataset_rows`;
  DOCTYPE rejected, stdlib ElementTree). **Tested live against OCA's public QA** (2026-10-14: test user
  `test@oca.com.ar`/`123456`, account `111757/001`, CUIT `30-53625919-4`): operativas, quote, create, PDF
  and ZPL labels, tracking, cancel, branches. Account: e-Pak user/password, account number
  (`client_code`), CUIT (`sender_document`, needed to quote), `extra` cost center / pickup slot /
  origin branch. Contracts = **operativas**; "Probar conexión" returns them (`GetOperativasByUsuario`,
  with `ASucursal` → kind branch, `OrigenSucursal` → `origin: "branch"`). Create =
  `IngresoORMultiplesRetiros_v2` confirmed (**needs the undocumented `compatMode` param**): `group_number`
  = OrdenRetiro (labels and cancel use it), tracking = 19-digit NumeroEnvio. XML attributes are cut by
  UTF-8 **bytes** (`fit`) and the province goes **without accents, uppercase, CABA = "CAPITAL FEDERAL"**
  (`oca_province`): "Ciudad Autónoma de Buenos Aire" fails with "Error al actualizar @TabEnvios".
  No public IdEstado table: mapped by text (`classify`); confirmed 1 = en proceso de retiro, 54 =
  cancelado. `OCA_TRACKING_URL_TEMPLATE` A CONFIRMAR.
- **Correo Argentino (`correo_argentino/`)** — **two APIs, decided 2026-10-14**: **Paq.ar** (`/paqar/v1`,
  agreement in `client_code` + API key in `password`, headers `Authorization: Apikey`/`agreement`) creates
  orders, labels (`POST /labels?labelFormat=10x15`, base64 PDF), tracking (`GET /tracking` with a JSON
  body), cancel (`PATCH /orders/{tn}/cancel`) and agencies; it does NOT quote. **MiCorreo**
  (`/micorreo/v1`, its API user/password → JWT kept in `token_encrypted`, plus `customer_id`) only quotes
  (`/rates`). Without MiCorreo credentials it dispatches without a price. Contracts = service type +
  delivery: stored as `CP-D`/`CP-S` (`normalize_contracts`), the merchant types `CP`. Paq.ar takes ONE
  parcel per order (total weight), a declared value (`extra.declared_value`), and province letters for
  sender and (home) recipient. Written WITHOUT credentials from the official manuals (Paq.ar 2.0, Apr
  2023; MiCorreo 2025): **A CONFIRMAR** the create response field (`trackingNumber` assumed), the
  `order` wrapper (the manual's example and schema disagree; schema used), `labelFormat` as query
  param, `stateId` format and the status texts. Tests against `FakeCorreo`.
- Frontend: **one account page for every carrier**, `transportista.html?carrier=<code>` +
  `assets/js/transportista.js`, built from `carrier_info` (fields, contract labels, package fields,
  push section). `andreani.html` now only redirects there. In Mis pedidos the strip lists every carrier
  with its state and the "Despachar" dialog picks the carrier (hidden when only one is ready; the last
  one used is remembered in localStorage), its services, branches and quote — all through the common API.
- **Price in the store checkout for every carrier** (2026-10-14): `checkout.CARRIERS` = Andreani's own
  module + a `CarrierCheckout(provider)` per provider with `supports_checkout` (Correo, OCA), all with the
  same shape (`OPTION_CODE`, `DEFAULT_NAME`, `config`/`save_config`/`account_for`/`problems`/
  `checkout_rate`/`test_quote`), so `carrier_rates`, Tiendanube's carrier options and the views don't care
  which. The provider supplies `checkout_quote(account, contract, cp, kg, timeout)` (OCA: Tarifar with the
  CUIT; Correo: MiCorreo `/rates` type D) and `checkout_problems`. Config per store in
  `preferences["<code>_checkout"]`; cache/timeouts `CARRIER_CHECKOUT_*`; buyer price and weight rounding
  shared in `pricing.py`. API `GET/PUT carriers/checkout/` + `POST carriers/checkout/test/` (with
  `carrier`; `checkout_views.py`, whose validation/saving Andreani's old `andreani/checkout/` view now
  reuses). Page `checkout_transportistas.html` (one card per store, one collapsible block per carrier
  with an account); `checkout_andreani.html` only redirects there.
- **Trying Correo without credentials** (2026-10-22): `python manage.py fake_correo_server` (launch config
  `fake-correo`) serves the tests' `FakeCorreo` on `http://127.0.0.1:8098` — both APIs under `/paqar/v1` and
  `/micorreo/v1`, real PDF labels saying "PRUEBA", and each tracking query advances one step (imposición →
  en tránsito → entregado). Point `CORREO_ARGENTINO_PAQAR_BASE_QA`/`CORREO_ARGENTINO_MICORREO_BASE_QA` there in
  `.env`, restart the backend, and save the account in QA with agreement `18017`, API key `clave-paqar`, service
  `CP`, MiCorreo `api-user`/`api-pass`, customer `0090000025`. Local only.
- Not done yet: real-account checks of every "A CONFIRMAR".

#### Andreani

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
  package_count?}`, `orders.dispatch`, one failing order never blocks the rest) stores the shipment and,
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
  (+ `checkout/test/`, uncached) and the common `checkout_transportistas.html`. Both callbacks still call
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
  login deactivates the account (`last_error`, shown in its account page) until the client saves it again;
  any other failure is noted and the shipment is retried on the next interval, not every loop.
- Frontend — **everything Andreani lives in "Mis pedidos", no menu entries of its own** (decided
  2026-10-09: separate "Envíos/Cuenta/Checkout Andreani" buttons made the user pick the same orders in
  another page and still type the tracking by hand in `despachar.html`). In `pedidos.html`:
  `assets/js/pedidos/envios.js` (`window.OrderShipping`) draws, per order, the carrier + tracking link +
  Andreani's status (from `GET shipments/`, latest non-cancelled per order), the buttons "Despachar" /
  "Imprimir" / "Actualizar seguimiento" / "Cancelar envío", the checkbox + a selection bar floating at the bottom (fixed since 2026-10-29: in the flow above the list it pushed the orders down ~50px when it appeared, and the next click landed on another order)
  ("Despachar (n)", "Imprimir (n)") and a strip with each carrier's account state linking to
  `transportista.html?carrier=…` and `checkout_transportistas.html` (reached only from there, topbar back to
  `pedidos.html`). Since 2026-10-26 that strip is a quiet grey line (green-dot chip per ready carrier, "Precio en el
  checkout", "Conectar otro") when everything works, and an amber notice only when an existing account needs
  attention ("Revisar X") or no carrier is ready. Both dialogs refresh the list from their own close() (and from the `close` event, for
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
  from anywhere. JS for the checkout page in `assets/js/andreani/` (the account page is the common one).
- **Trying it without credentials**: `python manage.py fake_andreani_server` (`apps/carriers/management/`)
  serves `FakeAndreani` on `http://127.0.0.1:8099` (real PDF labels saying "PRUEBA"; every tracking query
  advances one step: admitted → in transit → delivered; shipment numbers start from the clock so a
  restart never repeats one already in the DB). Set `ANDREANI_API_BASE_QA=http://127.0.0.1:8099`
  in `.env`, restart the backend, and save the account in QA with `cliente-prueba` /
  `Clave-Andreani-1` and contract `400006709` (home) or `400006710` (branch). Local only.
- Push "novedades": done in the common layer (see above). Not done: Warehouse (stock management is not
  part of the product for now).

### Frontend

Plain multi-page app, one HTML file per screen, no framework/bundler. Admin pages
(`gestionuser.html`/`roles.html`/`reportes.html`/`auditoria.html`/`pedidos_admin.html`/`soporte_admin.html`)
share the same script order — `config.js`, `auth.js`, `utils.js`, `admin_common.js`, `admin_sidebar.js`,
then the page's own script — and each redirects to `dashboard.html` if the caller lacks that page's
permission (UI-only gating; the backend re-checks every permission server-side). **They don't load Bootstrap**
(removed 2026-10-21: it overrode the font and size of labels/selects/dates, which is why the user filter
looked "raro"); `gestionuser.css` sets the app font on `body` and carries the few resets they took from it
(`[hidden]`, `.nav { display: flex }`, ...). No page loads Bootstrap since 2026-10-26: the access screens
(`index.html`, `register.html`, `logoutpage.html`) share `assets/css/acceso.css` (split screen, labels above the
fields, show/hide password via `data-reveal` in `utils.js`, errors inline in `#pageMessage` instead of a pop-up).
The old `index.css` is only kept for the PHP-era `index_test.html`.
In `gestionuser.html` "Crear usuario" is a centred window (`.create-panel` is the backdrop, Esc closes it).
Its column headers sort (2026-10-26; before they were decorative): click = sort, again = reverse, a third
time = back to normal, kept in sync with "Ordenar por" in the advanced filters. The API's `ordering` takes
`name` (what the Usuario column shows), `role` (admin → platform staff → merchant, same rule as
`get_effective_role`), `status`, `orders_count` and `last_order_at` besides the model fields; empty values go
last both ways. The bulk actions are a dark floating bar at the bottom (`.bulk-dock`, Esc or × clears).
"Exportar" is ONE button (2026-10-26; there were "Exportar", an .xlsx built in the browser from the ticked rows with
SheetJS from a CDN, and "Exportar CSV" from the server): a small panel asks which users (the whole filtered list, or
the ticked ones — preselected when there are) and the format, and the server builds both:
`GET /users/export/?file_type=csv|xlsx` (csv by default and unchanged; xlsx via `apps.common.spreadsheet.xlsx_bytes`).
Redesigned the same day (`assets/css/gestionuser_page.css`, `body.users-page`, so the other admin pages that share
`gestionuser.css` don't change): one search box (the table's), the four stat cards became one strip — the total
with an active/inactive bar plus Activos/Inactivos/Bloqueados as buttons that set the status/locked filters
(same selects as the filter panel; pressing again clears) —, the "Filtros" button shows how many filters are on,
and the email moved under the name (no Email column; email order stays in "Ordenar por").

- `assets/js/utils.js` — shared helpers loaded as a classic script (global functions, not an ES module):
  `escapeHtml`, `getErrorMessage`, `showMessage`, `formatDate`, `extractResults`, and since 2026-10-28
  `formatDateTime(value, empty="-")` (dd/mm/aaaa, hh:mm; admin_common no longer has its own),
  `addressSummary`, `showFieldMessage`, `fillStoreFilter(select, field, {minStores})` and
  `renderPrevNextPager(container, data, page, goTo)` — copies of them used to live in 3-5 pages each.
  `hasPermission` (dashboard/templates) and the page `formatDate`s were left on purpose: they behave
  differently. Geist is loaded with 400,500,600,700 (+ Geist Mono 400/500). `showMessage`
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
- `assets/js/topbar.js` — the shared top bar for the non-admin pages. Since 2026-10-26: "Panel" (home) and the
  business switcher on the left, the page's `data-links` in the middle as light links, and an account menu on the
  right (avatar → Mi cuenta, Mi actividad, Ayuda, Cerrar sesión). A link to `dashboard.html` or to something in the
  account menu is not repeated among the page links. Under 960px the page links move to a second row that scrolls
  sideways (they used to pile on top of each other). The page declares only
  `<header class="topbar" id="appTopbar" data-links='[{"href":"…","label":"…"}]'></header>`; the script
  fills it from the stored session on load, and a page holding a fresh user calls
  `window.AppTopbar.render(user)`. **The bar logs out by itself** (`logoutBtn` keeps its id, `stopImmediatePropagation`
  so a page's old listener doesn't run twice): re-rendering with the fresh user replaced the button and the page's
  listener was lost, so "Cerrar sesión" only worked sometimes. Styles in `ui.css` (`header.topbar.app-topbar`, more
  specific than each page's `.topbar`). Since 2026-10-26 `dashboard.html` uses it too ("Administración" as a page link
  with `perm: users.view`; `dashboard.js` renders it with the session user merged in, because the dashboard's user
  has no permission list and a link with `perm` shows when the list is unknown). The admin pages keep their own
  header from `admin_sidebar.js`.
- **Shared UI pieces (2026-10-21): `assets/css/ui.css` + the end of `utils.js`.** Every page links `ui.css` right
  after the favicon (`assets/images/favicon.svg`), before its own stylesheet. In it: the dialogs, the loading bars,
  the empty state, `.table-placeholder`, `button/input/select/textarea { font-family: inherit }` (buttons without
  their own rule came out in Arial), and the phone rules for the shared topbar (`header.topbar`, a bit more
  specific than each page's own rule so it wins).
  - **No `alert()`/`confirm()`/`prompt()`**: use `await appConfirm(msg)` / `appAlert(msg, {tone})` /
    `await appPrompt(msg, default)`. With no `confirmLabel`, the confirm button takes the question's verb
    ("¿Eliminar…" → "Eliminar"), red for the ones that delete or cut something. The way out says "Volver",
    because several questions are "¿Cancelar…?". `index.html`/`register.html` load `utils.js` for this.
  - Loading = `appSkeleton()` (grey bars) instead of a bare "Cargando...". Empty list = `appEmptyState({icon,
    title, text, actionLabel, actionHref})`, with its own line icons (`APP_EMPTY_ICONS`): not every page loads
    the icon font.
  - **Busy submit buttons for free**: a document-level `submit` listener (bubble phase, so after the form's own
    handler) disables the submitter and swaps its text ("Guardando…", "Creando…", "Enviando…"…) when the
    handler called `preventDefault` and didn't disable the button itself. It counts in-flight `fetch` calls
    (`window.fetch` is wrapped) and gives the button back when they end. A form that manages its own button
    (its own "Conectando…") is left alone.
  - `base.css` had lost the `@media (max-width: 640px)` around its last two rules when it was extracted, so
    the phone-only topbar wrap and `.main` padding applied on desktop too. Fixed.
- `assets/css/base.css` — reset, `body`/`.layout`/`.main`, top bar and buttons, loaded BEFORE the page's own
  stylesheet by the pages whose CSS had these rules byte-identical (`documentos`/`importar`/`integraciones`/
  `labels_shared`/`pedidos`/`perfil`). `tiendas.css`/`despachar.css`/`gestionuser.css` don't load it.
- **Design system (2026-10-29, redesign with the redesign/minimalist/design-system skills; user: "que se sienta
  consistente y bonito", identity renewed).** Everything visual is a token at the top of `ui.css`, which every page
  loads first; **page CSS never writes a hex color, font, font size, radius or shadow by hand** — it uses the tokens.
  Audit before: 116 hand-written colors, 28 font sizes, 18 radii, 35 shadows, buttons defined in 20 files.
  - **Identity**: font **Geist** (+ Geist Mono) from Google Fonts on every page (was Karla); warm "stone" neutrals
    `--n-0…--n-900`; ONE accent, the Human Log logo green `--accent-500` `#308028` (ramp 50-700; the app was orange
    and clashed with the green logo); state colors as muted pastels + ink: `--success-*`, `--danger-*`,
    `--warning-*`, `--info-*`. The app mark `favicon.svg` is green too. Store colors (`store-color-N` in
    `pedidos.css`) are identification colors and stay as they are.
  - **Scales**: `--text-xs…--text-2xl` (12/13/14/16/18/22/28), `--radius-sm|md|lg|xl|full` (6/8/12/16),
    `--shadow-xs|sm|md|lg` (tinted, near invisible), `--ring` (focus), `--page-form|content|wide` (800/1080/1400 for
    `.main`). Roles: `--bg --surface --surface-2 --line --line-strong --ink --ink-2 --muted --subtle
    --placeholder --field-border`. The old names (`--blue`, `--card`, `--border`, `--text-dark`, `--text-muted`,
    `--green`…) still exist as aliases of the new ones.
  - **Components, once, in `ui.css`**: `.btn` + `.btn-primary` (green) / `.btn-outline|-secondary|-cancel` /
    `.btn-ghost|-quiet` / `.btn-danger` / `.btn-small`, the page heading (`.main-heading h1/p`; the admin `.topbar
    h1/p` matches it), form fields (border `--field-border`, green border on focus, `accent-color` on checkboxes),
    `.page-message.error|success|info|warning`. Pages don't redefine them (the copies in base/app/despachar/
    tiendas/gestionuser were removed); a page that needs something else uses its own class.
  - **Contrast (WCAG AA, checked)**: `--muted` 4.6:1 on `--bg`, primary button 4.95:1, state pairs ≥ 4.8:1,
    field borders and `--n-400` icons 3:1, placeholders 3.7:1.
  - **Icons: Phosphor (regular) only** (2026-10-29, second pass): `@phosphor-icons/web@2.1.1` from jsDelivr, linked
    right after `ui.css` on every page; `<i class="ph ph-name" aria-hidden="true"></i>`, inheriting color and sized
    by the container (`.ph` base rule in `ui.css`; the old `... svg {width}` rules also cover `.ph`). Material Design
    Icons and the hand-drawn Lucide-style SVGs are gone; the only inline SVGs left are the column sort arrows of
    `gestionuser.html` (they change state through CSS) and the initials avatar. No emojis as icons.
  - **Selects** lose the native look: `select:not([multiple]):not([size])` in `ui.css` draws the system chevron
    (`!important` on the background image/position and `padding-right`, because page rules use the `background:`
    and `padding:` shorthands that would erase it).
  - The access flow (login, register, recuperar/reset password, verify-email, cambiar-password) shares the split
    `acceso.css` layout; its submit is the same green primary.
  - `editor_rotulos.html` maps its own variable names onto the tokens (no inline palette any more); the LABEL
    canvas keeps pure black on white — it mirrors the print.
  - Local dev serves files without cache headers: after a CSS change the browser can show the old one (worse with
    the manual `?v=` stamps). Hard reload; in the server `version_assets.sh` stamps by content.
- **`app.css` vs `pedidos.css` (2026-10-26).** `pedidos.css` used to be the de facto base of ~20 merchant pages that
  show no orders. Its generic part (top bar, buttons, `.main`, `.profile-card`, fields, `.form-message`,
  `.empty-state`) is now `assets/css/app.css`, loaded where `pedidos.css` was; `pedidos.css` keeps only order things
  (list and card, `.status-badge`, store colors/chips/tags, addresses, `.orders-pager`) and is loaded right after
  `app.css` only by the pages that show orders (pedidos, imprimir_rotulos, estado_pedidos, planilla_retiro,
  cargar_seguimientos, envios_andreani, direcciones). A new merchant page loads `app.css`; add `pedidos.css` only if
  it draws orders (also via `createOrderPicker`/`renderBulkResult`).
- **Page CSS only holds what differs (2026-10-26).** `.btn`/`.profile-card`/fields/messages were copied in ~12 page
  stylesheets; every merchant page now loads `base.css` + `app.css` before its own CSS, and those files keep only
  their real differences (usually `.main { max-width }`). The cleanup was checked by comparing computed styles of
  buttons, cards, titles and fields on 23 pages before and after. `.btn` in `app.css` carries `white-space: nowrap`,
  pressed and focus states; `.profile-card-title` wraps with a gap. In the label editor `.field` is a drawn label
  element, not a form field: `labels_shared.css` resets its margin (`.label .field`).
- `pedidos.html` is the work screen: only the order list (dispatch, see Carriers → Frontend); "+ Nuevo pedido" opens
  a dialog (`pedidos.html#nuevo` opens it on load) and the own addresses have their page, `direcciones.html` +
  `assets/js/direcciones.js` (2026-10-26). Each order card is compact: number + description on one line, the status
  history folded in "Historial (n)", and the action follows the status — "Despachar" (primary) while created/preparing,
  "Crear envío" (secondary) when it already shows dispatched but has no carrier shipment, "Cancelar pedido" as quiet
  red text. It paginates its order history (`?page=`, Anterior/Siguiente driven by the API's `next`).
  Its store filter is a sticky column on the left (one row per store with a square of its initials, plus
  "Todas" and "Cargados a mano"; a horizontal strip under 860px), built from
  `GET /integrations/stores/` — never a hardcoded list — and rebuilt when the tab becomes visible again,
  so a store connected in `tiendas.html` shows up by itself. Each store gets a color (`store-color-N` in
  `pedidos.css`, assigned by store id order so it doesn't shift when another store is added) that also
  marks its orders' left border. In "Todas" the page's orders are grouped by store (column order, manual
  last), each group with a header (2026-10-26). Styles in `assets/css/pedidos_tiendas.css`, loaded only
  here because `pedidos.css` is shared by ~18 pages; that card uses `overflow: clip`, not `hidden`, or the
  sticky store column stops sticking.
- `assets/js/admin_common.js` — permission/formatting helpers for admin pages: `isAdminMode()`,
  `canUseUserPermission()`, `canViewUsers()`, `translateRole()`, `actionLabel()`, `formatDateTime()`,
  `renderSimplePager()`, `loadAuditActionsCatalog()`.
- `assets/js/auth.js` — `window.Auth` (`apiFetch`, `hasSession`, `saveSession`, `withSession`,
  `getCurrentUser`, `logout`). The session is cookies the code never sees; `localStorage` keeps only `user`
  (not secret), which is also what `hasSession()` checks — every page's "not logged in → index.html" guard.
  `apiFetch` sends the cookies, retries once through a single shared refresh on 401, and on
  failure clears the session and redirects to `index.html` (throwing an error with `.isSessionExpired`).
  `index.html`/`register.html`/`cambiar-password.html`/`logoutpage.html` load it too.
- **"Olvidé mi contraseña" (two pages, both public).** `recuperar-password.html` +
  `assets/js/recuperar_password.js` asks for the email (`POST /auth/password-reset/`);
  `reset-password.html` + `assets/js/reset_password.js` takes `uid`/`token` off the query string — the
  link in the email — and posts the new one (`POST /auth/password-reset/confirm/`). Neither loads
  `auth.js`: there is no session to attach, which is the whole point. `PASSWORD_RESET_URL` in `.env` is
  what the email links to, and it must name the real file (`.../reset-password.html`) — its default
  (`{FRONTEND_URL}/reset-password`) matches nothing in `frontend/` and the link 404s. The login's
  "Olvidaste tu clave?" used to be `href="#!"`: the screen was promised and did not exist. Reused
  `index_test.html` still has the dead link and is left alone (PHP-era leftover).
- **`roles.html` layout (2026-10-21)**: tabs "Roles de la plataforma" (roles in a left column with "Nuevo rol" at
  its foot; the chosen role's permissions on the right as one card per category with a count and "Marcar todos",
  a search box, and Guardar/Descartar enabled only with unsaved changes — switching role or leaving asks first) and
  "Perfiles de los comercios" (`roles_perfiles.js`, read-only: every business with its team profiles, plus a table
  of which role permissions each team access gates) from `GET /api/v1/teams/profiles/?search=`
  (`AdminTeamProfilesView`, `users.view`, 50 businesses max). It shows that the two are layers, not duplicates:
  the role is the ceiling, the profile what the owner hands out inside it.
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
- `ayuda.html` ends with a short FAQ (`#preguntas`, native `<details>`, styles in `assets/css/ayuda.css`) below the
  support form and "Mis mensajes" (moved there 2026-10-21, user's request): the whole block folds (outer
  `<details>`, open by default) and so does each question; "Abrir todas/Cerrar todas" is in `ayuda.js`. The
  dashboard's Ayuda card links to it.
- `reportes.html` shows one block at a time with tabs (Usuarios · Seguridad · Pedidos · Soporte y actividad, the
  last only with `support.view_all`/`audit.view`); the chosen tab lives in the `#hash`. Its "Actualizar" and the
  one in `estado_sistema.html` say "Actualizando…" and then when they finished — before, refreshing with nothing
  changed looked like a dead button. The answers name buttons and sections as the
  app shows them — **if one is renamed, fix the FAQ too**.
- **Addresses are not a menu entry.** They have their own page, `direcciones.html` (full CRUD), reached from the
  new-order dialog ("Administrar direcciones") and the dashboard's function search; until 2026-10-26 they were a
  section of `pedidos.html`.
- **Manual orders need who receives (2026-10-28).** The address form had no `recipient_name`, so
  `build_label_context` fell back to the business name and a manual order's label read "Enviar a: <own business>".
  The form is now shared — `renderAddressFields`/`readAddressFields`/`fillAddressFields`/`missingAddressFields` in
  `utils.js` (recipient, street, city and postal code required) — and used by `direcciones.html` (which can now
  also edit) and by the "Nuevo pedido" dialog, whose "Enviar a" select ends in "+ Una dirección nueva…" to create
  it without leaving (saved to Mis direcciones). A saved address without a recipient is refused there. The API
  still accepts an address without one (existing tests post that), so the rule lives in the screen.
- **`[hidden]` always hides (2026-10-29)**: `ui.css` has `[hidden] { display: none !important; }`. Any rule with a
  `display` (`.btn`, a flex panel…) used to beat the attribute, so `hidden` elements stayed visible ("Quitar la foto"
  with no photo). Show/hide with the `hidden` property; something shown with `style.display` must not carry `hidden`.
- **Legal pages (2026-10-29, DRAFT)**: `privacidad.html` and `terminos.html` (public, no JS, `assets/css/legal.css`),
  written from what the app really stores and shares (buyer email/phone only from some platforms and never on the
  label; Anthropic only for photo import; Google Fonts/jsDelivr; no analytics). Company data is marked
  `.legal-todo` (yellow) and the whole text needs review by the company before publishing. Linked from the login,
  the register form ("Al crear tu cuenta aceptás…") and the end of `ayuda.html`. **If what the app stores or who it
  shares data with changes, update `privacidad.html`.**
- **Brand: "Human Log · Rótulos"** (company · product, decided 2026-10-28). Access pages show the cropped Human Log
  logo (`humanlog-logo.png` / `humanlog-lockup.png`, cut from `LogoLogin.png`; `HLLogoFinal.jpg`, the globe one, is
  no longer used) + "Rótulos"; the shared topbar's home link is the app mark (`favicon.svg`) + "Rótulos" and
  `topbar.js` appends " · Human Log Rótulos" to the tab title; the admin sidebar says "Rótulos / Administración";
  mail subjects and the default sender say "Human Log · Rótulos". Since the 2026-10-29 redesign the app accent IS the
  logo green (see "Design system").
- `editor_rotulos.html?nueva=plantilla` (reached from the gallery, `galeria_plantillas.html`) titles the page "Nueva plantilla" and its
  main button saves a template ("Guardar plantilla", same path as "Guardar como plantilla"); plain
  `editor_rotulos.html` still saves a `Label`.

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
- Roles: admin y subscriber ("Comerciante") + roles personalizados de personal de la plataforma. Legacy
  `"user"`, `"designer"` y `"operator"` = `"subscriber"`. Admin = grupo `admin` + `is_staff`.
