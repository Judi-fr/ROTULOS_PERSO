// Shopify en tiendas.html: conectar con el dominio de la tienda o con la app
// propia del comerciante (conexión manual). Se carga DESPUÉS de tiendas.js y usa sus funciones y constantes
// (apiFetch, INTEGRATIONS_API, showConnectResult, connectError, connectStore,
// loadStores, createElement...); tiendas.js llama a lo de acá recién al dibujar
// las tiendas, cuando este archivo ya cargó.
//
// Backend: apps/integrations/providers/shopify/.

const SHOPIFY_MANUAL_URL = `${INTEGRATIONS_API}/shopify/connect-manual/`;

function shopifyShop() {
  const input = document.getElementById("shopifyShopInput");
  const shop = input.value.trim();
  if (!shop) {
    showConnectResult("Escribí el dominio de tu tienda Shopify (termina en .myshopify.com) en el campo de arriba.");
    input.focus();
  }
  return shop;
}

function connectShopify(event) {
  event.preventDefault();
  const shop = shopifyShop();
  if (shop) connectStore("shopify", { button: document.getElementById("connectShopifyBtn"), shop });
}

// Camino manual de Shopify: el backend pide un token con las credenciales de
// la app del comerciante antes de guardar nada. Nunca se guardan en el navegador.
async function connectShopifyManual(event) {
  event.preventDefault();
  const shop = shopifyShop();
  if (!shop) return;
  const idInput = document.getElementById("shopifyClientIdInput");
  const secretInput = document.getElementById("shopifyClientSecretInput");
  if (!idInput.value.trim() || !secretInput.value.trim()) {
    showConnectResult("Pegá el ID de cliente y el secreto de tu app.");
    return;
  }
  const button = document.getElementById("shopifyManualBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Conectando...";
  try {
    const response = await apiFetch(SHOPIFY_MANUAL_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shop, client_id: idInput.value.trim(), client_secret: secretInput.value.trim() }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw connectError(getErrorMessage(data, "No se pudo conectar la tienda con tu app."), response.status);
    }
    idInput.value = "";
    secretInput.value = "";
    document.getElementById("shopifyManual").open = false;
    showConnectResult(
      `¡Listo! ${data.name || "Tu tienda"} quedó conectada con tu app. Estamos importando sus pedidos: pueden tardar unos minutos en aparecer.`,
      "success"
    );
    await loadStores();
  } catch (err) {
    if (err.isSessionExpired) return;
    showConnectResult(
      err.code ? err.message : "No pudimos comunicarnos con el servidor. Revisá tu conexión y probá de nuevo.",
      "error",
      err.code || null
    );
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

document.getElementById("connectShopifyForm")?.addEventListener("submit", connectShopify);
document.getElementById("shopifyManualForm")?.addEventListener("submit", connectShopifyManual);
