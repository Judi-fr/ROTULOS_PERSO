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

// --- Nuestro módulo (fase 2) ------------------------------------------------
// Opcional: sin él los pedidos entran por el repaso cada 5 minutos. Con él
// llegan al instante, se imprime/despacha desde Ventas → Pedidos y se cotiza en
// el checkout. Como el plugin de WooCommerce, "Verificar módulo" le pide al
// backend que le escriba la configuración (store_print + admin_module.py).
// print_plugin_linked: true = vinculado, false = no está o falta el permiso,
// null = todavía no se sabe.
const MAGENTO_MODULE_URL = `${INTEGRATIONS_API}/magento/module/`;

function renderMagentoModule(store) {
  const wrapper = createElement("div", "store-sender store-plugin");
  wrapper.appendChild(createElement("h4", "store-sender-title", "Módulo para Magento"));
  const linked = store.print_plugin_linked === true;
  wrapper.appendChild(
    createElement(
      "p",
      "store-sender-help",
      linked
        ? "Los pedidos llegan al instante. En Ventas → Pedidos tildá pedidos y elegí en Acciones “Imprimir rótulos”, “Planilla de retiro” o “Despachar con tu transportista”. Para cotizar en el checkout, activá el método de envío “Rótulos de envío (tabla de tarifas)” en Tiendas → Configuración → Ventas → Métodos de envío."
        : "Opcional: con nuestro módulo los pedidos llegan al instante (sin él, cada 5 minutos), imprimís y despachás desde tu lista de pedidos y cotizás el envío en tu checkout."
    )
  );
  if (!linked) {
    const steps = createElement("ol", "woo-steps");
    steps.appendChild(createElement("li", "", "Descargá el módulo y descomprimilo en la raíz de tu Magento (crea app/code/Rotulos/Envio)."));
    steps.appendChild(
      createElement("li", "", "Activalo: bin/magento module:enable Rotulos_Envio, después setup:upgrade y cache:flush (lo hace quien administra tu servidor).")
    );
    steps.appendChild(
      createElement(
        "li",
        "",
        "En Sistema → Extensiones → Integraciones, abrí la integración de la app, tildá “Rótulos de envío” en su API y reautorizala."
      )
    );
    steps.appendChild(createElement("li", "", "Volvé acá y tocá “Verificar módulo”."));
    wrapper.appendChild(steps);
  }

  const feedback = createElement("p", "store-sender-msg");
  feedback.hidden = true;
  if (store.print_plugin_linked === false) {
    showFeedback(feedback, "Todavía no encontramos el módulo, o la integración no tiene el permiso “Rótulos de envío”.", false);
  }
  wrapper.appendChild(feedback);

  const actions = createElement("div", "store-actions");
  const download = createElement("button", linked ? "btn btn-outline btn-small" : "btn btn-primary btn-small", "Descargar el módulo");
  download.type = "button";
  download.addEventListener("click", () => downloadMagentoModule(download, feedback));
  actions.appendChild(download);
  const check = createElement("button", "btn btn-outline btn-small", "Verificar módulo");
  check.type = "button";
  check.addEventListener("click", () => checkMagentoModule(store, check, feedback));
  actions.appendChild(check);
  wrapper.appendChild(actions);
  return wrapper;
}

async function downloadMagentoModule(button, feedback) {
  button.disabled = true;
  try {
    const response = await apiFetch(MAGENTO_MODULE_URL);
    if (!response.ok) throw new Error(`Error ${response.status}: no se pudo descargar el módulo.`);
    await downloadResponse(response, "rotulos-envio-magento.zip");
    showFeedback(feedback, "Módulo descargado. Ahora descomprimilo en tu Magento y activalo.", true);
  } catch (err) {
    if (err.isSessionExpired) return;
    showFeedback(feedback, err.message || "No se pudo descargar el módulo.", false);
  } finally {
    button.disabled = false;
  }
}

async function checkMagentoModule(store, button, feedback) {
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Verificando...";
  try {
    const response = await apiFetch(`${STORES_URL}${store.id}/check-print-plugin/`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo verificar el módulo."));
    await loadStores();
    if (data.print_plugin_linked === true) {
      showStoreFeedback(store.id, ".store-plugin", "El módulo quedó vinculado: los pedidos ya llegan al instante.", true);
    } else {
      showStoreFeedback(
        store.id,
        ".store-plugin",
        "Todavía no encontramos el módulo, o la integración no tiene el permiso “Rótulos de envío”.",
        false
      );
    }
  } catch (err) {
    if (err.isSessionExpired) return;
    showFeedback(feedback, err.message || "No se pudo verificar el módulo.", false);
    button.disabled = false;
    button.textContent = originalText;
  }
}
