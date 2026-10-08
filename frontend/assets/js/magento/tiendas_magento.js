// Magento en tiendas.html: conectar con las credenciales de una Integración y
// reconectar. Se carga DESPUÉS de tiendas.js y usa sus funciones (apiFetch,
// showConnectResult, connectError, loadStores, setConnectCardOpen); tiendas.js
// llama a openMagentoReconnect recién al dibujar las tiendas.
//
// Backend: apps/integrations/providers/magento/.

const MAGENTO_CONNECT_URL = `${INTEGRATIONS_API}/magento/connect-manual/`;

// Magento: las cuatro credenciales de la Integración se prueban contra la
// tienda en el backend antes de guardarse. Nunca se guardan en el navegador.
const MAGENTO_CREDENTIAL_INPUTS = {
  consumer_key: "magentoConsumerKeyInput",
  consumer_secret: "magentoConsumerSecretInput",
  access_token: "magentoAccessTokenInput",
  access_token_secret: "magentoAccessTokenSecretInput",
};

async function connectMagento(event) {
  event.preventDefault();
  const siteInput = document.getElementById("magentoSiteInput");
  const button = document.getElementById("magentoConnectBtn");
  if (!siteInput.value.trim()) {
    showConnectResult("Escribí la dirección de tu tienda Magento (por ejemplo, https://mitienda.com).");
    siteInput.focus();
    return;
  }
  const body = { site_url: siteInput.value.trim() };
  Object.entries(MAGENTO_CREDENTIAL_INPUTS).forEach(([field, id]) => {
    body[field] = document.getElementById(id).value.trim();
  });
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Conectando...";
  try {
    const response = await apiFetch(MAGENTO_CONNECT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw connectError(getErrorMessage(data, "No se pudo conectar la tienda Magento con esas credenciales."), response.status);
    }
    Object.values(MAGENTO_CREDENTIAL_INPUTS).forEach((id) => {
      document.getElementById(id).value = "";
    });
    document.getElementById("magentoConnect").open = false;
    showConnectResult(
      `¡Listo! ${data.name || "Tu tienda"} quedó conectada. Estamos importando sus pedidos: pueden tardar unos minutos en aparecer.`,
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

// Magento tampoco tiene URL de autorización: reconectar es volver a pegar las
// credenciales, con la dirección ya escrita.
function openMagentoReconnect(store) {
  setConnectCardOpen(true);
  const form = document.getElementById("magentoConnect");
  form.open = true;
  document.getElementById("magentoSiteInput").value = store.store_url;
  form.scrollIntoView({ behavior: "smooth", block: "start" });
  document.getElementById("magentoConsumerKeyInput").focus();
}

document.getElementById("magentoConnectForm")?.addEventListener("submit", connectMagento);
