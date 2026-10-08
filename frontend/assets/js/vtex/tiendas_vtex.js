// VTEX en tiendas.html: conectar con la clave de aplicación, reconectar y el
// aviso de la tabla de tarifas publicada. Se carga DESPUÉS de tiendas.js y usa
// sus funciones (apiFetch, showConnectResult, connectError, loadStores,
// setConnectCardOpen, createElement); tiendas.js llama a openVtexReconnect y
// renderRatesPush recién al dibujar las tiendas, cuando este archivo ya cargó.
//
// Backend: apps/integrations/providers/vtex/.

const VTEX_CONNECT_URL = `${INTEGRATIONS_API}/vtex/connect-manual/`;

// VTEX: la clave de aplicación se prueba contra VTEX en el backend antes de
// guardarse. Nunca se guarda en el navegador.
async function connectVtex(event) {
  event.preventDefault();
  const accountInput = document.getElementById("vtexAccountInput");
  const keyInput = document.getElementById("vtexAppKeyInput");
  const tokenInput = document.getElementById("vtexAppTokenInput");
  const button = document.getElementById("vtexConnectBtn");
  if (!accountInput.value.trim()) {
    showConnectResult("Escribí el nombre de tu cuenta VTEX (lo que va antes de .myvtex.com).");
    accountInput.focus();
    return;
  }
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Conectando...";
  try {
    const response = await apiFetch(VTEX_CONNECT_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        account: accountInput.value.trim(),
        app_key: keyInput.value.trim(),
        app_token: tokenInput.value.trim(),
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw connectError(getErrorMessage(data, "No se pudo conectar la cuenta VTEX con esa clave."), response.status);
    }
    keyInput.value = "";
    tokenInput.value = "";
    document.getElementById("vtexConnect").open = false;
    const warnings = Array.isArray(data.warnings) && data.warnings.length ? ` Ojo: ${data.warnings.join(" ")}` : "";
    showConnectResult(
      `¡Listo! ${data.name || "Tu cuenta VTEX"} quedó conectada. Estamos importando sus pedidos: pueden tardar unos minutos en aparecer.${warnings}`,
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

// VTEX no tiene URL de autorización: reconectar es volver a pegar una clave
// en su formulario, con la cuenta ya escrita.
function openVtexReconnect(store) {
  setConnectCardOpen(true);
  const form = document.getElementById("vtexConnect");
  form.open = true;
  document.getElementById("vtexAccountInput").value = store.external_store_id;
  form.scrollIntoView({ behavior: "smooth", block: "start" });
  document.getElementById("vtexAppKeyInput").focus();
}

// Plataformas que cotizan con una tabla propia (VTEX): la tabla se publica
// desde Tarifas de envío; acá solo se ve si está publicada.
function renderRatesPush(store) {
  const state = store.rates_push || {};
  const text = state.enabled
    ? "Tu tabla de tarifas está publicada en el checkout de VTEX. "
    : "Para cotizar el envío en tu checkout de VTEX, cargá tus precios y publicalos desde ";
  const wrapper = createElement("p", "connect-help store-rates-push", text);
  const link = createElement("a", "", state.enabled ? "Ver tarifas de envío" : "Tarifas de envío");
  link.href = `tarifas_envio.html?store=${encodeURIComponent(store.id)}`;
  wrapper.appendChild(link);
  if (!state.enabled) wrapper.appendChild(document.createTextNode("."));
  return wrapper;
}

document.getElementById("vtexConnectForm")?.addEventListener("submit", connectVtex);
