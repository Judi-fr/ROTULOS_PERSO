#!/bin/sh
# Pone a cada CSS y JS propio, en los .html, un ?v=<hash de su contenido>.
#
# nginx deja los assets en la caché del navegador una hora: sin versión, un
# comerciante que ya tenía la app abierta seguía con el CSS o el JS viejo
# hasta una hora después de actualizar (y con el HTML nuevo, que no siempre
# combina). Con el hash, un archivo que cambió tiene otra URL y se baja de
# nuevo; uno que no cambió sigue saliendo de la caché.
#
# Lo corre el Dockerfile al armar la imagen, sobre su copia de los archivos
# (nunca sobre el repo). Uso: version_assets.sh <carpeta del frontend>
# Reemplaza también los ?v= escritos a mano. config.js no se toca: se genera
# al arrancar el contenedor y nginx lo sirve sin caché.
set -e

root="${1:-.}"
cd "$root"

find assets/css assets/js -type f \( -name '*.css' -o -name '*.js' \) \
    ! -path 'assets/js/config.js' ! -path 'assets/apis/*' | while read -r file; do
    hash=$(md5sum "$file" | cut -c1-10)
    pattern=$(printf '%s' "$file" | sed 's/[.]/\\./g')
    # "assets/css/ui.css" o "assets/css/ui.css?v=lo-que-sea", seguido de comilla.
    sed -i "s#\(${pattern}\)\(?v=[^\"']*\)\{0,1\}\([\"']\)#\1?v=${hash}\3#g" ./*.html
done

echo "Assets versionados en $(ls ./*.html | wc -l) páginas."
