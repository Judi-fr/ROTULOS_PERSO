# Imagen del frontend de ROTULOS_PERSO.
#
# No hay etapa de build porque no hay nada que compilar: el frontend es HTML,
# CSS y JS planos (ver CLAUDE.md). Lo único que pasa al arrancar es que se
# genera config.js con la URL de la API del entorno.
# Nombre completo del registro: Docker lo resuelve igual, y Podman (que es lo
# que hay en esta máquina) falla con el nombre corto.
FROM docker.io/library/nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY . /usr/share/nginx/html

# nginx:alpine ya trae un usuario sin privilegios; el puerto es 8080 y no 80
# para poder correr sin root.
COPY docker-entrypoint.sh /docker-entrypoint.d/99-config-js.sh
RUN chmod +x /docker-entrypoint.d/99-config-js.sh \
    && rm -f /usr/share/nginx/html/Dockerfile /usr/share/nginx/html/nginx.conf \
             /usr/share/nginx/html/docker-entrypoint.sh

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/index.html || exit 1
