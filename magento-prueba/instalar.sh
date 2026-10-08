#!/usr/bin/env bash
# Instala un Magento de prueba (Mage-OS) dentro del Codespace. Se corre una sola
# vez: si ya está instalado, solo lo vuelve a levantar. Tarda 20-30 minutos la
# primera vez (descarga ~1 GB de código).
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${CODESPACE_NAME:-}" ]; then
  echo "Este script está pensado para correr dentro de un GitHub Codespace." >&2
  exit 1
fi
URL="https://${CODESPACE_NAME}-8080.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}/"

as_web() { docker compose exec -T -u www-data -e COMPOSER_HOME=/tmp/composer -e COMPOSER_MEMORY_LIMIT=-1 web "$@"; }

echo "==> Levantando MariaDB, OpenSearch y PHP/Apache..."
docker compose up -d --build

echo "==> Esperando a la base de datos y al buscador..."
until docker compose exec -T db mariadb-admin ping -umagento -pmagento --silent >/dev/null 2>&1; do sleep 3; done
until docker compose exec -T web curl -fs http://opensearch:9200 >/dev/null 2>&1; do sleep 3; done

docker compose exec -T web chown www-data:www-data /var/www/html

if ! as_web test -f bin/magento; then
  echo "==> Descargando Mage-OS (Magento Open Source, sin cuenta de Adobe)..."
  as_web composer create-project --no-interaction --repository-url=https://repo.mage-os.org/ \
    mage-os/project-community-edition .
fi

if ! as_web test -f app/etc/env.php; then
  ADMIN_PASSWORD="Adm$(openssl rand -hex 6)9"
  echo "==> Instalando Magento en ${URL} ..."
  as_web bin/magento setup:install \
    --base-url="${URL}" --base-url-secure="${URL}" --use-secure=1 --use-secure-admin=1 --use-rewrites=1 \
    --db-host=db --db-name=magento --db-user=magento --db-password=magento \
    --search-engine=opensearch --opensearch-host=opensearch --opensearch-port=9200 \
    --admin-firstname=Admin --admin-lastname=Prueba --admin-email=admin@example.com \
    --admin-user=admin --admin-password="${ADMIN_PASSWORD}" --backend-frontname=admin \
    --language=es_AR --currency=ARS --timezone=America/Argentina/Buenos_Aires

  # Sin segundo factor en el admin: es una tienda de prueba y el 2FA pide un
  # email que acá no sale. (Mage-OS puede no traer el módulo de Adobe IMS.)
  as_web bin/magento module:disable Magento_AdminAdobeImsTwoFactorAuth Magento_TwoFactorAuth \
    || as_web bin/magento module:disable Magento_TwoFactorAuth || true
  # Acá no hay servidor de correo: sin esto, cada email que Magento intenta
  # mandar (el aviso de envío, por ejemplo) deja un error en el log.
  as_web bin/magento config:set system/smtp/disable 1
  as_web bin/magento deploy:mode:set developer
  as_web bin/magento setup:upgrade
  as_web bin/magento cache:flush

  printf 'Admin de Magento: %sadmin\nUsuario: admin\nContraseña: %s\n' "${URL}" "${ADMIN_PASSWORD}" > credenciales-admin.txt
  echo "==> Credenciales del admin guardadas en magento-prueba/credenciales-admin.txt"
fi

echo "==> Haciendo público el puerto 8080 (para que el backend de la laptop llegue)..."
if ! gh codespace ports visibility 8080:public -c "${CODESPACE_NAME}" >/dev/null 2>&1; then
  echo "    No se pudo automáticamente: en la pestaña PORTS, clic derecho en 8080 → Port Visibility → Public."
fi

echo
echo "Listo. Tienda: ${URL}"
echo "Admin:  ${URL}admin   (usuario y contraseña en credenciales-admin.txt)"
echo "Para crear pedidos de prueba: python3 crear_pedidos.py"
