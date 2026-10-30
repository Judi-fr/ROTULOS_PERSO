#!/usr/bin/env bash
# Restaura un backup hecho con scripts/backup.sh. ÚLTIMO RECURSO: reemplaza la base
# entera; se pierde todo lo que entró después del backup (pedidos de las tiendas
# incluidos, ver docs/despliegue.md → "Estrategia de rollback").
#
#   scripts/restore.sh backups/db-20261030-040000.dump
#   scripts/restore.sh backups/db-20261030-040000.dump backups/media-20261030-040000.tar.gz
#
# Para la API, el worker y el monitor mientras restaura (nadie escribe a medias)
# y los vuelve a levantar al final. Pide escribir RESTAURAR para seguir.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.prod.yml)

db_file="${1:-}"
media_file="${2:-}"
if [[ -z "$db_file" || ! -f "$db_file" ]]; then
    echo "Uso: $0 <db-....dump> [<media-....tar.gz>]" >&2
    exit 1
fi
if [[ -n "$media_file" && ! -f "$media_file" ]]; then
    echo "No existe $media_file" >&2
    exit 1
fi

DB_USER="${POSTGRES_USER:-rotulos}"
DB_NAME="${POSTGRES_DB:-rotulos}"
if [[ -f .env ]]; then
    DB_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2- || true)"; DB_USER="${DB_USER:-rotulos}"
    DB_NAME="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2- || true)"; DB_NAME="${DB_NAME:-rotulos}"
fi

# Que el archivo sea un dump legible ANTES de parar nada.
"${COMPOSE[@]}" exec -T db pg_restore --list < "$db_file" > /dev/null \
    || { echo "$db_file no es un backup de la base legible." >&2; exit 1; }
if [[ -n "$media_file" ]]; then
    gzip -t "$media_file" || { echo "$media_file está dañado." >&2; exit 1; }
fi

echo "Se va a REEMPLAZAR la base '$DB_NAME' con $db_file${media_file:+ y la carpeta media con $media_file}."
read -r -p "Escribí RESTAURAR para seguir: " answer
[[ "$answer" == "RESTAURAR" ]] || { echo "Cancelado."; exit 1; }

# Antes de pisar nada, un backup de cómo está ahora (por si el que se restaura era el equivocado).
scripts/backup.sh antes-de-restaurar

"${COMPOSE[@]}" stop api worker monitor
# Si algo falla de acá en adelante, los servicios vuelven a arrancar igual (con la
# base como haya quedado: pg_restore va en una sola transacción, así que entera o intacta).
trap 'echo "La restauración FALLÓ; se vuelven a levantar los servicios." >&2; "${COMPOSE[@]}" start api worker monitor' ERR

"${COMPOSE[@]}" exec -T db pg_restore -U "$DB_USER" -d "$DB_NAME" --clean --if-exists --no-owner \
    --single-transaction < "$db_file"

if [[ -n "$media_file" ]]; then
    # La API está parada: se usa un contenedor de un solo uso con el mismo volumen.
    "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh api \
        -c 'rm -rf /app/media/* && tar -C /app -xzf -' < "$media_file"
fi

trap - ERR
"${COMPOSE[@]}" start api worker monitor
echo "Restaurado. Revisá /api/v1/health/system/ y volvé a sincronizar las tiendas (ver docs/despliegue.md)."
