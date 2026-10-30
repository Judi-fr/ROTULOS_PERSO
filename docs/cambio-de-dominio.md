# Pasar a un dominio nuevo (del túnel de la laptop al servidor)

**Estado (2026-10-29):** todo lo de afuera apunta todavía al túnel de Tailscale de la
laptop (`sillaenojada-hp-laptop-14-dq1xxx.tail0c0717.ts.net`). Si la laptop se apaga, Shopify y
Tiendanube no pueden llegar a la app. Esta lista es lo que hay que cambiar el día que el
servidor tenga dominio. En los ejemplos, `rotulos.ejemplo.com.ar` es el dominio nuevo.

El orden importa: primero el servidor (para que el dominio nuevo conteste), después cada
plataforma, al final las tiendas ya conectadas.

## 1. Servidor (`.env` junto a `docker-compose.prod.yml`)

| Variable | Valor |
|---|---|
| `DOMINIO` | `rotulos.ejemplo.com.ar` (Caddy saca el certificado y el frontend arma `API_BASE` con esto) |
| `ALLOWED_HOSTS` | `rotulos.ejemplo.com.ar` |
| `FRONTEND_URL` | `https://rotulos.ejemplo.com.ar` |
| `INTEGRATIONS_PUBLIC_BASE_URL` | `https://rotulos.ejemplo.com.ar` (webhooks, callbacks, links de impresión, push de transportistas) |
| `PASSWORD_RESET_URL` | `https://rotulos.ejemplo.com.ar/reset-password.html` |
| `EMAIL_VERIFICATION_URL` | `https://rotulos.ejemplo.com.ar/verify-email.html` |
| `CORS_ALLOWED_ORIGINS` | `https://rotulos.ejemplo.com.ar` (con Caddy todo es el mismo origen, pero la variable es obligatoria) |

`TEAM_INVITATION_URL` sale sola de `FRONTEND_URL`. Comprobar: `https://rotulos.ejemplo.com.ar/api/v1/health/ready/`
contesta 200 y la página de login abre.

## 2. Shopify

1. En `rotulos-extension/shopify.app.rotulos-perso.toml` reemplazar el dominio del túnel en las
   tres líneas: `application_url`, el `uri` de los webhooks (incluye los de privacidad,
   `compliance_topics`) y `redirect_urls`.
2. Desde `rotulos-extension/`: `shopify app deploy` (sale una versión nueva, p. ej. `rotulos-perso-9`)
   y publicarla.
3. La extensión de impresión no lleva dominio (usa una URL relativa que Shopify resuelve con
   `application_url`), así que no hay que tocarla.
4. Las tiendas Shopify ya instaladas: ver el paso 6.

## 3. Tiendanube (Portal de Partners → la app)

- **URL de redirección**: `https://rotulos.ejemplo.com.ar/api/v1/integrations/tiendanube/callback/`
- **Links de acciones masivas** (Ventas): los tres, cada uno a su página:
  - Imprimir rótulos → `https://rotulos.ejemplo.com.ar/imprimir_tiendanube.html`
  - Planilla de retiro → `https://rotulos.ejemplo.com.ar/planilla_tiendanube.html`
  - Despachar con tu transportista → `https://rotulos.ejemplo.com.ar/despachar_tiendanube.html`
- **Webhooks de privacidad** (store/redact, customers/redact, customers/data_request):
  `https://rotulos.ejemplo.com.ar/api/v1/integrations/tiendanube/webhooks/`
- Si el formulario de la app pide política de privacidad o términos:
  `https://rotulos.ejemplo.com.ar/privacidad.html` y `.../terminos.html`.

## 4. Google (login con Google, Google Cloud Console)

- Credenciales → el cliente OAuth web → **Orígenes de JavaScript autorizados**: agregar
  `https://rotulos.ejemplo.com.ar` (dejar el de desarrollo si se sigue usando).
- Pantalla de consentimiento: dominio autorizado `ejemplo.com.ar`, página principal,
  **política de privacidad** `https://rotulos.ejemplo.com.ar/privacidad.html` y **términos**
  `https://rotulos.ejemplo.com.ar/terminos.html`.
- El client id (`GOOGLE_CLIENT_ID` en el `.env` y en `frontend/index.html`) no cambia.

## 5. WooCommerce, Magento y VTEX (solo si hay tiendas conectadas)

- **WooCommerce y Magento** se actualizan solos: en cada reconciliación el backend le vuelve a
  escribir al plugin/módulo las URLs (`configure_admin_print`); para no esperar, "Verificar plugin"
  en `tiendas.html`. Sus webhooks nuevos los registra el paso 6; los viejos fallan y WooCommerce
  los desactiva solo.
- **VTEX**: el hook con el dominio viejo se toma como "de otro sistema" y no se pisa (para no
  romperle el hook a un ERP que use la misma clave), así que la tienda queda con el aviso de pedir
  una clave propia. Los pedidos igual entran por el feed. Hasta resolverlo en el código, borrar el
  hook viejo a mano en VTEX y correr el paso 6. (Hoy no hay tiendas VTEX reales.)

## 6. Tiendas ya conectadas: `update_store_urls`

En el servidor, con el `.env` nuevo cargado:

```bash
docker compose -f docker-compose.prod.yml exec api python manage.py update_store_urls --dry-run
docker compose -f docker-compose.prod.yml exec api python manage.py update_store_urls
```

Encola para cada tienda activa el registro de webhooks con la URL nueva (lo hace el worker; de paso
reimporta los pedidos recientes, sin duplicar) y en las de Tiendanube con medio de envío dado de alta
actualiza en el momento los callbacks de cotización y de rótulos. Si una falla lo dice y sigue con
las demás.

## 7. Lo que tiene que hacer cada comerciante

- **Agente de impresión**: `rotulos_agent.json` guarda la dirección del servidor. Hay que cambiar
  `server` a `https://rotulos.ejemplo.com.ar` en cada PC (o crear un agente nuevo en
  `impresoras.html`, que baja el archivo ya armado) y reiniciar el agente.
- **Push de novedades de Andreani**: la URL que se le pasó a Andreani tiene el dominio viejo.
  La página del transportista ya muestra la URL nueva (se arma con `INTEGRATIONS_PUBLIC_BASE_URL`):
  hay que mandársela al comercial de Andreani.
- Los links de mails ya enviados (invitaciones, recuperar contraseña) apuntan al dominio viejo:
  vencen solos; se vuelven a pedir.
