# Magento de prueba en GitHub Codespaces

Una tienda Magento para probar la integración de ROTULOS_PERSO (fase 1:
conexión, importación, repaso y despacho) sin pagar un servidor y sin cargar la
laptop. Usa **Mage-OS**, la versión comunitaria de Magento Open Source: mismo
código y misma API, pero se descarga sin cuenta de Adobe.

Corre en un Codespace de 2 núcleos y 8 GB (gratis: unas 60 horas por mes en una
cuenta personal). La laptop solo corre lo de siempre: el backend y el worker.

> Este es un repo **aparte** del de ROTULOS_PERSO. No lo mezcles con la rama `marco`.

## 1. Crear el repo y el Codespace (una sola vez)

1. En GitHub: **New repository** → nombre `magento-prueba` → **Private** → Create.
2. En la página del repo vacío: **uploading an existing file** → arrastrá **todo el
   contenido** de esta carpeta, incluida la carpeta `.devcontainer` (si el navegador
   no muestra las carpetas ocultas, arrastrá la carpeta `magento-prueba` entera
   desde el explorador de archivos) → **Commit changes**.
3. En el repo: botón verde **Code** → pestaña **Codespaces** → **Create codespace on main**.
   Si te pregunta el tipo de máquina, elegí **2-core / 8 GB**.

## 2. Instalar Magento (una sola vez, 20-30 minutos)

En la terminal del Codespace (abajo):

```bash
bash instalar.sh
```

Al terminar muestra la dirección de la tienda (`https://...-8080.app.github.dev/`)
y deja el usuario y la contraseña del admin en `credenciales-admin.txt`.

Si dice que no pudo hacer público el puerto: pestaña **PORTS** → clic derecho en
el **8080** → **Port Visibility** → **Public**. Sin eso, el backend de la laptop
no llega a la tienda.

## 3. Crear pedidos de prueba

```bash
python3 crear_pedidos.py
```

Crea 3 pedidos con direcciones argentinas (`python3 crear_pedidos.py 10` para
más). Para probar una cancelación: `python3 crear_pedidos.py --cancelar 000000002`.

## 4. Crear la Integración en Magento (lo que haría un comerciante)

El admin queda en inglés (Mage-OS no trae traducción al español):

1. Entrá a `https://...-8080.app.github.dev/admin` con las credenciales de `credenciales-admin.txt`.
2. **System → Extensions → Integrations → Add New Integration**.
3. **Name**: `Rotulos`. **Your Password**: la del admin.
4. Pestaña **API** → **Resource Access: Custom** → marcá **Sales → Operations → Orders**
   (con **Actions → View, Ship, Comment**) y **Sales → Operations → Shipments**.
5. **Save** → en la lista, **Activate** → **Allow**. Copiá las cuatro credenciales
   (Consumer Key, Consumer Secret, Access Token, Access Token Secret).

## 5. Conectarla desde ROTULOS_PERSO (en la laptop)

1. Levantá el backend y el **worker** (sin el worker no entran los pedidos).
2. En `tiendas.html` → **Conectar una tienda** → **Magento**: pegá la dirección
   de la tienda y las cuatro credenciales.
3. En unos minutos los pedidos aparecen en **Mis pedidos**.

Qué probar:
- **Importación:** los 3 pedidos aparecen con nombre, dirección y provincia bien.
- **Repaso:** creá otro pedido con `crear_pedidos.py` → aparece solo en ≤ 5 minutos.
- **Cancelación:** cancelá uno con `--cancelar` → en la app pasa a cancelado.
- **Despacho:** despachá un pedido en la app con correo, número y URL de seguimiento
  → en el admin de Magento el pedido tiene un **Shipment** con el tracking y un
  comentario con la URL.
- **Ya despachado desde Magento:** creá el Shipment a mano en el admin (sin tracking)
  y después despachalo en la app → se agrega el tracking al mismo Shipment.

Magento acá **no manda emails** (no hay servidor de correo y se apagaron a propósito),
así que el aviso al comprador no se puede ver: se prueba con una tienda real.

## Al terminar

Un Codespace se apaga solo a los 30 minutos sin uso y conserva todo: para retomar,
abrilo desde github.com/codespaces y corré `bash instalar.sh` (solo vuelve a levantar
los contenedores). Si no lo vas a usar más, **borralo** desde github.com/codespaces
para no gastar las horas gratis.
