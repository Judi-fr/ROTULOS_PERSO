#!/bin/sh
# Escribe assets/js/config.js con la URL real de la API antes de arrancar
# nginx.
#
# El frontend no tiene build: `config.js` es un archivo suelto con la URL
# hardcodeada, que en desarrollo apunta a 127.0.0.1:8000. En un servidor eso
# no sirve — el navegador del comerciante resolvería 127.0.0.1 contra SU
# máquina —, así que el valor se inyecta acá, al arrancar, y la misma imagen
# sirve para cualquier entorno.
set -e

: "${API_BASE:=http://127.0.0.1:8000/api/v1}"

cat > /usr/share/nginx/html/assets/js/config.js <<JS
// GENERADO AL ARRANCAR EL CONTENEDOR — no editar acá.
// El valor sale de la variable de entorno API_BASE (ver docker-entrypoint.sh).
window.APP_CONFIG = {
  API_BASE: "${API_BASE}",
};
JS

echo "config.js generado con API_BASE=${API_BASE}"
# Sin `exec`: este script lo corre el entrypoint oficial de nginx desde
# /docker-entrypoint.d/, y es ESE el que después arranca el servidor. Si acá
# hiciéramos exec, nginx nunca llegaría a levantar.
