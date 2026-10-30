#!/usr/bin/env bash
# Backup de producción: la base (pg_dump) y los archivos subidos/generados (media).
#
# Se corre en el servidor, desde la carpeta del repo (donde está docker-compose.prod.yml):
#   scripts/backup.sh                 # backup ahora
#   scripts/backup.sh antes-de-v1.2   # con una etiqueta en el nombre (p. ej. antes de un deploy)
#
# Variables (opcionales, se leen del entorno o del .env):
#   BACKUP_DIR      carpeta destino (default: ./backups)
#   BACKUP_KEEP     cuántos backups de cada tipo se conservan (default: 14)
#   BACKUP_S3_URI   si está, se copia cada archivo ahí con `aws s3 cp` (p. ej. s3://rotulos-backups/prod)
#
# Para correrlo todos los días a las 4:00 (crontab -e del usuario que maneja docker):
#   0 4 * * * cd /ruta/al/repo && scripts/backup.sh >> backups/backup.log 2>&1
#
# Restaurar: scripts/restore.sh <archivo .dump> [<archivo media .tar.gz>]
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.prod.yml)

if [[ -f .env ]]; then
    # Solo las variables que usa este script; el .env puede tener valores con espacios.
    while IFS='=' read -r key value; do
        case "$key" in
            BACKUP_DIR|BACKUP_KEEP|BACKUP_S3_URI|POSTGRES_USER|POSTGRES_DB)
                [[ -z "${!key:-}" ]] && export "$key=$value" ;;
        esac
    done < <(grep -E '^(BACKUP_DIR|BACKUP_KEEP|BACKUP_S3_URI|POSTGRES_USER|POSTGRES_DB)=' .env || true)
fi

BACKUP_DIR="${BACKUP_DIR:-backups}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"
DB_USER="${POSTGRES_USER:-rotulos}"
DB_NAME="${POSTGRES_DB:-rotulos}"

label="${1:-}"
label="${label//[^A-Za-z0-9._-]/-}"
stamp="$(date +%Y%m%d-%H%M%S)${label:+-$label}"
umask 077  # tienen datos de compradores: solo el dueño los lee
mkdir -p "$BACKUP_DIR"

db_file="$BACKUP_DIR/db-$stamp.dump"
media_file="$BACKUP_DIR/media-$stamp.tar.gz"

echo "[$(date -Is)] Backup de la base → $db_file"
# Se escribe a un .partial y se renombra al final: un backup cortado a la mitad
# nunca queda con el nombre de uno bueno (ni cuenta para la rotación).
"${COMPOSE[@]}" exec -T db pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$db_file.partial"
# Un dump vacío o ilegible es peor que ninguno: se verifica que pg_restore lo lea.
"${COMPOSE[@]}" exec -T db pg_restore --list < "$db_file.partial" > /dev/null
mv "$db_file.partial" "$db_file"

echo "[$(date -Is)] Backup de media → $media_file"
"${COMPOSE[@]}" exec -T api tar -C /app -czf - media > "$media_file.partial"
gzip -t "$media_file.partial"
mv "$media_file.partial" "$media_file"

if [[ -n "${BACKUP_S3_URI:-}" ]]; then
    echo "[$(date -Is)] Copiando a $BACKUP_S3_URI"
    aws s3 cp "$db_file" "${BACKUP_S3_URI%/}/" --only-show-errors
    aws s3 cp "$media_file" "${BACKUP_S3_URI%/}/" --only-show-errors
fi

# Rotación: quedan los BACKUP_KEEP más nuevos de cada tipo (solo en el disco local;
# en S3 la retención se maneja con una regla de ciclo de vida del bucket).
for prefix in db media; do
    ls -1t "$BACKUP_DIR/$prefix-"* 2>/dev/null | grep -v '\.partial$' \
        | tail -n +"$((BACKUP_KEEP + 1))" | xargs -r rm -f --
done

echo "[$(date -Is)] Listo: $(du -h "$db_file" | cut -f1) base, $(du -h "$media_file" | cut -f1) media"
