# Rotulos_Envio — módulo de Magento 2

Lo baja el comerciante desde **Tiendas** en la app (tienda Magento → "Descargar el módulo").
Es opcional: sin él la tienda funciona igual (los pedidos entran por el repaso cada 5 minutos).

Con el módulo:

- **Los pedidos llegan al instante**: cada pedido guardado avisa a la app.
- **Ventas → Pedidos**: tres acciones masivas nuevas — *Imprimir rótulos*, *Planilla de retiro* y
  *Despachar con tu transportista*.
- **Checkout**: un método de envío "Rótulos de envío (tabla de tarifas)" que cotiza con la tabla
  de tarifas de la app (y la cotización de tu transportista, si la activaste).

## Instalación

1. Descomprimí el zip en la raíz de Magento (crea `app/code/Rotulos/Envio/`).
2. Corré:

   ```
   bin/magento module:enable Rotulos_Envio
   bin/magento setup:upgrade
   bin/magento setup:di:compile      # solo en modo producción
   bin/magento cache:flush
   ```

3. **Sistema → Extensiones → Integraciones** → la integración que creaste para la app → *API* →
   tildá **Rótulos de envío** (los dos permisos) y guardá. Magento pide **reautorizar** la
   integración: hacelo y, si te cambia el token de acceso, volvé a pegarlo en la app.
4. En la app, **Tiendas** → tu tienda → **Verificar módulo**. La app le escribe al módulo a dónde
   avisar y con qué firmar: no hay nada que completar a mano.
5. Para cotizar en el checkout: **Tiendas → Configuración → Ventas → Métodos de envío → Rótulos de
   envío (tabla de tarifas)** → *Activo: Sí*.

## Cómo habla con la app

Todo va de servidor a servidor, firmado con HMAC-SHA256 (header `X-Rotulos-Signature`) con un
secreto por tienda que escribe la app por `POST /rest/V1/rotulos/config` (permiso
`Rotulos_Envio::config`). Del comprador no viaja nada más que el id del pedido: la app lo lee por la
API REST con los permisos de la integración.

Escrito sin una tienda Magento a mano (2026-10-22): A CONFIRMAR en la primera tienda real.
