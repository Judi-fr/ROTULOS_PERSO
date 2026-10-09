// WooCommerce en tiendas.html: conectar (autorización automática o claves a
// mano) y el plugin de WordPress para imprimir desde su lista de pedidos. Se carga DESPUÉS de tiendas.js y usa sus funciones y constantes
// (apiFetch, INTEGRATIONS_API, showConnectResult, connectError, connectStore,
// loadStores, createElement...); tiendas.js llama a lo de acá recién al dibujar
// las tiendas, cuando este archivo ya cargó.
//
// Backend: apps/integrations/providers/woocommerce/.

const WOO_MANUAL_URL = `${INTEGRATIONS_API}/woocommerce/connect-manual/`;
const WOO_PLUGIN_URL = `${INTEGRATIONS_API}/woocommerce/print-plugin/`;

function wooSite() {
  const input = document.getElementById("wooSiteInput");
  const site = input.value.trim();
  if (!site) {
    showConnectResult(
      "Escribí la dirección de tu tienda WooCommerce (por ejemplo, https://mitienda.com) en el campo de arriba."
    );
    input.focus();
  }
  return site;
}

function connectWoo(event) {
  event.preventDefault();
  const site = wooSite();
  if (site) connectStore("woocommerce", { button: document.getElementById("connectWooBtn"), shop: site });
}

// Camino manual: las claves se prueban contra la tienda en el backend antes
// de guardarse. Nunca se guardan en el navegador.
async function connectWooManual(event) {
  event.preventDefault();
  const site = wooSite();
  if (!site) return;
  const keyInput = document.getElementById("wooKeyInput");
  const secretInput = document.getElementById("wooSecretInput");
  const button = document.getElementById("wooManualBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Conectando...";
  try {
    const response = await apiFetch(WOO_MANUAL_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        site_url: site,
        consumer_key: keyInput.value.trim(),
        consumer_secret: secretInput.value.trim(),
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw connectError(getErrorMessage(data, "No se pudo conectar la tienda con esas claves."), response.status);
    }
    keyInput.value = "";
    secretInput.value = "";
    document.getElementById("wooManual").open = false;
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

// ---------------------------------------------------------------------------
// Plugin de WordPress para imprimir desde WooCommerce
// ---------------------------------------------------------------------------

// El plugin agrega a las acciones masivas de WooCommerce → Pedidos "Imprimir
// rótulos", "Imprimir planilla de retiro" y "Despachar con Andreani" (desde la
// 1.3.0). El comerciante no lo configura: "Verificar plugin" le pide al
// backend que le escriba la configuración (si no, lo hace el repaso cada 30
// minutos). print_plugin_linked: true = vinculado, false = no está instalado,
// null = todavía no se sabe.
function renderPrintPlugin(store) {
  const wrapper = createElement("div", "store-sender store-plugin");
  wrapper.appendChild(createElement("h4", "store-sender-title", "Imprimir desde WooCommerce"));

  const linked = store.print_plugin_linked === true;
  wrapper.appendChild(
    createElement(
      "p",
      "store-sender-help",
      linked
        ? "En WooCommerce → Pedidos tildá los pedidos y elegí en Acciones masivas “Imprimir rótulos”, “Imprimir planilla de retiro” o “Despachar con Andreani”. Si no ves las dos últimas, bajá el plugin de nuevo y subilo encima del que tenés."
        : "Instalá nuestro plugin en tu WordPress para imprimir los rótulos desde tu lista de pedidos, sin entrar acá."
    )
  );

  if (linked) {
    // El mismo plugin cotiza el envío en el checkout, pero eso lo activa el
    // comerciante: un método de envío de WooCommerce vive en sus zonas.
    const rates = createElement(
      "p",
      "store-sender-help",
      "Para cotizar el envío en tu checkout: cargá tus precios en Tarifas de envío y, en WooCommerce → " +
        "Ajustes → Envío, agregá el método “Rótulos de envío (tabla de tarifas)” a tus zonas. "
    );
    const ratesLink = createElement("a", "store-link", "Ir a Tarifas de envío");
    ratesLink.href = `tarifas_envio.html?store=${encodeURIComponent(store.id)}`;
    rates.appendChild(ratesLink);
    wrapper.appendChild(rates);
  }

  if (!linked) {
    const steps = createElement("ol", "woo-steps");
    steps.appendChild(createElement("li", "", "Descargá el plugin."));
    const upload = createElement("li", "", "En tu WordPress andá a Plugins → Añadir nuevo → Subir plugin, elegí el archivo y activalo. ");
    if (store.store_url && /^https:\/\//i.test(store.store_url)) {
      const link = createElement("a", "store-link", "Abrir esa pantalla en tu tienda");
      link.href = `${store.store_url.replace(/\/+$/, "")}/wp-admin/plugin-install.php?tab=upload`;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      upload.appendChild(link);
    }
    steps.appendChild(upload);
    steps.appendChild(createElement("li", "", "Volvé acá y tocá “Verificar plugin”."));
    wrapper.appendChild(steps);
  }

  const feedback = createElement("p", "store-sender-msg");
  feedback.hidden = true;
  if (store.print_plugin_linked === false) {
    showFeedback(feedback, "Todavía no encontramos el plugin en tu tienda.", false);
  }
  wrapper.appendChild(feedback);

  const actions = createElement("div", "store-actions");
  const download = createElement("button", linked ? "btn btn-outline btn-small" : "btn btn-primary btn-small", "Descargar plugin");
  download.type = "button";
  download.addEventListener("click", () => downloadPrintPlugin(download, feedback));
  actions.appendChild(download);
  const check = createElement("button", "btn btn-outline btn-small", "Verificar plugin");
  check.type = "button";
  check.addEventListener("click", () => checkPrintPlugin(store, check, feedback));
  actions.appendChild(check);
  wrapper.appendChild(actions);

  return wrapper;
}

async function downloadPrintPlugin(button, feedback) {
  button.disabled = true;
  try {
    const response = await apiFetch(WOO_PLUGIN_URL);
    if (!response.ok) throw new Error(`Error ${response.status}: no se pudo descargar el plugin.`);
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = "rotulos-envio.zip";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    showFeedback(feedback, "Plugin descargado (rotulos-envio.zip). Ahora subilo en tu WordPress.", true);
  } catch (err) {
    if (err.isSessionExpired) return;
    showFeedback(feedback, err.message || "No se pudo descargar el plugin.", false);
  } finally {
    button.disabled = false;
  }
}

async function checkPrintPlugin(store, button, feedback) {
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Verificando...";
  try {
    const response = await apiFetch(`${STORES_URL}${store.id}/check-print-plugin/`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo verificar el plugin."));
    await loadStores();
    if (data.print_plugin_linked === true) {
      showStoreFeedback(store.id, ".store-plugin", "El plugin quedó vinculado: ya podés imprimir desde WooCommerce.", true);
    } else {
      showStoreFeedback(
        store.id,
        ".store-plugin",
        "Todavía no encontramos el plugin en tu tienda. Revisá que esté subido y activado.",
        false
      );
    }
  } catch (err) {
    if (err.isSessionExpired) return;
    showFeedback(feedback, err.message || "No se pudo verificar el plugin.", false);
    button.disabled = false;
    button.textContent = originalText;
  }
}

document.getElementById("connectWooForm")?.addEventListener("submit", connectWoo);
document.getElementById("wooManualForm")?.addEventListener("submit", connectWooManual);
