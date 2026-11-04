#!/usr/bin/env bash
# Despliegue de una versión en el servidor (T#183). Lo llama el pipeline
# azure-pipelines-deploy.yml, pero se puede correr a mano igual.
#
# Estructura en el servidor (DEPLOY_PATH, p. ej. /srv/rotulos):
#   shared/.env         el .env de producción (permisos 600; lo escribe el pipeline)
#   shared/backups/     los backups de scripts/backup.sh
#   releases/<versión>/ el código de cada versión desplegada
#   current, previous   archivos con la versión activa y la anterior
#
# Uso (desde la carpeta de la versión nueva, ya descomprimida en releases/<versión>):
#   scripts/deploy.sh <versión>     despliega esa versión
#   scripts/deploy.sh --rollback    vuelve a la versión anterior (sin tocar la base)
#
# Pasos: backup → build → migraciones → levantar → chequeo de salud. Si algo
# falla después de migrar, vuelve solo a la versión anterior. Las migraciones
# tienen que ser compatibles hacia atrás (docs/despliegue.md, "Rollback"): la
# versión anterior arranca contra la base ya migrada.
#
# Variables opcionales:
#   DEPLOY_PATH            raíz de la estructura (default: dos carpetas arriba de este release)
#   DEPLOY_HEALTH_SECONDS  cuánto esperar a que la API quede sana (default: 180)
#   DEPLOY_KEEP_RELEASES   cuántas versiones se guardan en releases/ (default: 5)
set -euo pipefail

release_dir="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_PATH="${DEPLOY_PATH:-$(cd "$release_dir/../.." && pwd)}"
HEALTH_SECONDS="${DEPLOY_HEALTH_SECONDS:-180}"
KEEP_RELEASES="${DEPLOY_KEEP_RELEASES:-5}"
shared="$DEPLOY_PATH/shared"
releases="$DEPLOY_PATH/releases"

log() { echo "[deploy $(date +%H:%M:%S)] $*"; }
fail() { echo "[deploy] ERROR: $*" >&2; exit 1; }

[[ $# -eq 1 ]] || fail "uso: scripts/deploy.sh <versión> | --rollback"
[[ -f "$shared/.env" ]] || fail "falta $shared/.env (lo escribe el pipeline desde el archivo seguro de Azure DevOps)"
mkdir -p "$shared/backups"

current="$(cat "$DEPLOY_PATH/current" 2>/dev/null || true)"
previous="$(cat "$DEPLOY_PATH/previous" 2>/dev/null || true)"

# Corre docker compose sobre el código de una versión, con sus imágenes
# etiquetadas con esa versión (RELEASE_TAG en docker-compose.prod.yml). El
# nombre del proyecto es fijo, así que todas las versiones usan los mismos
# volúmenes (base, media, certificados).
compose_in() {
    local version="$1"; shift
    (cd "$releases/$version" && RELEASE_TAG="$version" docker compose -f docker-compose.prod.yml "$@")
}

link_env() {
    ln -sfn "$shared/.env" "$releases/$1/.env"
}

dominio() {
    grep -E '^DOMINIO=' "$shared/.env" | tail -1 | cut -d= -f2- | tr -d '"' || true
}

# La API sana (su healthcheck consulta la base) y el sistema sin "error"
# visto desde afuera, a través de Caddy. Lo segundo cubre también el frontend
# y el certificado.
wait_healthy() {
    local version="$1" deadline=$((SECONDS + HEALTH_SECONDS)) api_id status domain code
    domain="$(dominio)"
    while (( SECONDS < deadline )); do
        api_id="$(compose_in "$version" ps -q api)"
        status="$(docker inspect -f '{{.State.Health.Status}}' "$api_id" 2>/dev/null || echo unknown)"
        if [[ "$status" == healthy ]]; then
            [[ -z "$domain" ]] && { log "API sana (sin DOMINIO en el .env, no se chequea desde afuera)"; return 0; }
            code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
                --resolve "$domain:443:127.0.0.1" "https://$domain/api/v1/health/system/" || echo 000)"
            if [[ "$code" == 200 ]]; then
                log "API sana y https://$domain/api/v1/health/system/ responde 200"
                return 0
            fi
            log "API sana; health/system/ responde $code, espero…"
        else
            log "API: $status, espero…"
        fi
        sleep 5
    done
    return 1
}

switch_to() {
    local version="$1"
    log "levantando $version"
    link_env "$version"
    compose_in "$version" up -d --remove-orphans
}

if [[ "$1" == "--rollback" ]]; then
    [[ -n "$previous" && -d "$releases/$previous" ]] || fail "no hay versión anterior guardada para volver"
    log "rollback de ${current:-?} a $previous (la base no se toca)"
    switch_to "$previous"
    wait_healthy "$previous" || fail "la versión anterior tampoco quedó sana: revisar a mano (docker compose logs)"
    echo "$previous" > "$DEPLOY_PATH/current"
    echo "$current" > "$DEPLOY_PATH/previous"
    log "listo: activa $previous"
    exit 0
fi

version="$1"
[[ "$version" =~ ^[A-Za-z0-9._-]+$ ]] || fail "versión inválida: $version"
[[ "$release_dir" == "$releases/$version" ]] || fail "este script tiene que correr desde $releases/$version (está en $release_dir)"
[[ "$version" != "$current" ]] || log "la versión $version ya es la activa: se vuelve a desplegar"
link_env "$version"

# 1. Backup antes de tocar nada (si ya hay una versión corriendo).
if [[ -n "$current" ]] && [[ -n "$(compose_in "$version" ps -q db 2>/dev/null)" ]]; then
    log "backup antes de desplegar"
    BACKUP_DIR="$shared/backups" "$releases/$version/scripts/backup.sh" "antes-de-${version:0:12}"
else
    log "primer despliegue: no hay base para respaldar"
fi

# 2. Imágenes nuevas. Si falla acá, la versión anterior sigue andando intacta.
log "construyendo imágenes"
compose_in "$version" build

# 3. Migraciones en un paso aparte, antes de reemplazar la API: si fallan, se
# corta acá y la versión anterior sigue atendiendo.
log "migraciones"
compose_in "$version" up -d db
compose_in "$version" run --rm --no-deps api python manage.py migrate --noinput \
    || fail "fallaron las migraciones de $version; sigue andando ${current:-nada (primer despliegue)}"

# 4 y 5. Levantar y verificar; si no queda sana, volver a la anterior.
switch_to "$version"
if ! wait_healthy "$version"; then
    echo "[deploy] ERROR: $version no quedó sana en ${HEALTH_SECONDS}s" >&2
    compose_in "$version" logs --tail 80 api >&2 || true
    if [[ -n "$current" && "$current" != "$version" && -d "$releases/$current" ]]; then
        log "volviendo a $current"
        switch_to "$current"
        wait_healthy "$current" || echo "[deploy] ERROR: $current tampoco quedó sana: revisar a mano" >&2
    fi
    exit 1
fi

# 6. Registrar qué quedó activo y limpiar versiones viejas.
if [[ -n "$current" && "$current" != "$version" ]]; then
    echo "$current" > "$DEPLOY_PATH/previous"
    previous="$current"
fi
echo "$version" > "$DEPLOY_PATH/current"
keep_versions=" $version ${previous:-} "
mapfile -t old < <(ls -1t "$releases" | tail -n +"$((KEEP_RELEASES + 1))")
for dir in "${old[@]}"; do
    [[ "$keep_versions" == *" $dir "* ]] && continue
    log "borrando la versión vieja $dir"
    rm -rf "${releases:?}/$dir"
    docker image rm "rotulos-api:$dir" "rotulos-web:$dir" >/dev/null 2>&1 || true
done
log "listo: activa $version${previous:+ (anterior: $previous)}"
