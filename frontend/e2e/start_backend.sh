#!/bin/sh
# La API para las pruebas de navegador: base propia, datos de demo y mails a la consola.
set -e
LOG="$(cd "$(dirname "$0")" && pwd)/backend.log"
cd "$(dirname "$0")/../../backend"
PYTHON="${E2E_PYTHON:-venv/bin/python}"
DB="$(mktemp -d)/e2e.sqlite3"
export DATABASE_URL="sqlite:///$DB"
export EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
export DJANGO_SETTINGS_MODULE=config.settings.dev
export SECRET_KEY="${SECRET_KEY:-solo-para-pruebas-de-navegador}"
export NOTICES_MAIL_ENABLED=False
"$PYTHON" manage.py migrate --noinput >/dev/null
"$PYTHON" manage.py seed_demo >/dev/null
"$PYTHON" manage.py shell < ../frontend/e2e/seed_e2e.py >/dev/null
exec "$PYTHON" manage.py runserver --noreload "127.0.0.1:$1" >"$LOG" 2>&1
