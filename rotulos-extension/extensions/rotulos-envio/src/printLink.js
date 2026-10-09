// Lo común a las tres opciones de la app en el admin de Shopify (imprimir
// rótulos, planilla de retiro, despachar con Andreani): pedirle al backend el
// enlace al PDF para los pedidos elegidos. El backend es
// backend/apps/integrations/providers/shopify/views.py (ShopifyPrintLinkView)
// y store_print.py; ``action`` es "labels", "manifest" o "dispatch".
//
// Ruta relativa: según la documentación, Shopify la resuelve contra la App
// URL de shopify.app.rotulos-perso.toml, así que la extensión no lleva el
// dominio del servidor escrito y no hay que tocarla al cambiar de servidor.
//
// Probando desde la máquina que corre el túnel de Tailscale, el dominio
// resuelve a su IP privada (100.x) y el navegador bloquea el pedido
// ("local network access": NetworkError, sin código de estado, nada en el
// servidor). Hay que desactivar el DNS de Tailscale o probar desde otro
// dispositivo.
export const PRINT_LINK_URL = "/api/v1/integrations/shopify/print-link/";

// El ID token de Shopify se agrega a mano en vez de esperar que Shopify lo
// sume solo: no dependemos de qué considera "el dominio de la app".
export async function requestLink(ids, action = "labels") {
  const token = await shopify.auth.idToken();
  const response = await fetch(PRINT_LINK_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? {Authorization: `Bearer ${token}`} : {}),
    },
    body: JSON.stringify({ids, action}),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  return body;
}
