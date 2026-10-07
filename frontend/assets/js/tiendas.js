// Tiendas conectadas (backend: apps.integrations).
//
// - Conectar Tiendanube / Shopify / WooCommerce: pide la URL de autorización
//   y lleva al comerciante a su plataforma. Shopify y WooCommerce necesitan
//   la dirección de la tienda. WooCommerce además se puede conectar pegando
//   las claves de la API (connectWooManual) y Shopify con una app propia del
//   comerciante (connectShopifyManual). Tiendanube no tiene camino manual: su
//   API solo da acceso a una app instalada.
// - Vuelta de la instalación: el backend redirige a esta página con
//   ?store_connected=<id>, ?store_claim=<token> o ?store_error=<código>
//   (ver StoreOAuthCallbackView). Un store_claim se canjea para vincular la
//   tienda a la cuenta; si no hay sesión, se guarda y se retoma después del
//   login (index.html vuelve acá cuando existe pendingStoreClaim).
// - Lista de tiendas con su estado, errores y la acción de desconectar.
// - Tiendas WooCommerce: el plugin de WordPress para imprimir rótulos desde
//   su lista de pedidos (descargarlo, subirlo, "Verificar plugin").
//
// Sesión y apiFetch salen de assets/js/auth.js (window.Auth). Todo texto
// que viene del backend (nombre, errores) se inserta con textContent.

const INTEGRATIONS_API = `${window.APP_CONFIG.API_BASE}/integrations`;
const STORES_URL = `${INTEGRATIONS_API}/stores/`;
const TEMPLATES_URL = `${window.APP_CONFIG.API_BASE}/labels/templates/`;
const PENDING_CLAIM_KEY = "pendingStoreClaim";
const PLATFORM_LABELS = { tiendanube: "Tiendanube", shopify: "Shopify", woocommerce: "WooCommerce" };
const WOO_MANUAL_URL = `${INTEGRATIONS_API}/woocommerce/connect-manual/`;
const WOO_PLUGIN_URL = `${INTEGRATIONS_API}/woocommerce/print-plugin/`;
const SHOPIFY_MANUAL_URL = `${INTEGRATIONS_API}/shopify/connect-manual/`;

const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

// La vuelta del OAuth no dice de qué plataforma venía: los textos no la nombran.
const STORE_ERROR_MESSAGES = {
  missing_code: "La plataforma no devolvió el código de autorización. Probá conectar la tienda de nuevo.",
  authorization_cancelled: "Se canceló la autorización: la tienda no se conectó.",
  invalid_state: "El enlace de instalación venció o no es válido. Volvé a tocar “Conectar”.",
  invalid_signature: "No pudimos verificar que la instalación venga de la plataforma. Volvé a tocar “Conectar”.",
  invalid_shop: "El dominio de la tienda no es válido. Revisalo y volvé a intentar.",
  authorization_rejected: "La plataforma rechazó la autorización (el código venció o ya se usó). Probá de nuevo.",
  provider_unavailable: "No pudimos comunicarnos con la plataforma. Probá de nuevo en unos minutos.",
  owned_by_other_account: "Esa tienda ya está vinculada a otra cuenta. Si es tuya, escribinos desde Ayuda / Soporte.",
};

const STATUS_CLASSES = { active: "ok", error: "warn", revoked: "off" };

// ---------------------------------------------------------------------------
// Utilidades de presentación
// ---------------------------------------------------------------------------

// showMessage y getErrorMessage salen de assets/js/utils.js. formatDate
// queda propia: usa Intl dateStyle/timeStyle corto en vez del formato
// dd/mm/aaaa hh:mm de utils.js.
function formatDate(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" });
}

function createElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

// Cartel con el resultado de conectar una tienda, debajo de los formularios
// de conexión (el #pageMessage de arriba queda fuera de la vista desde ahí).
// code: el número de error (estado HTTP) o el código que devolvió la
// plataforma; se muestra para que el comerciante lo pueda citar en soporte.
function showConnectResult(text, type = "error", code = null) {
  setConnectCardOpen(true);
  const el = document.getElementById("connectMessage");
  if (!el) {
    showMessage(code ? `Error ${code}: ${text}` : text, type);
    return;
  }
  el.replaceChildren();
  if (type === "error" && code) {
    el.appendChild(createElement("span", "connect-message-code", `Error ${code}: `));
  }
  el.appendChild(document.createTextNode(text));
  el.className = `page-message connect-message ${type}`;
  el.hidden = false;
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// "Conectar una tienda" es un desplegable: con tiendas ya conectadas arranca
// cerrado (lo que se busca suele ser "Mis tiendas"), sin ninguna arranca
// abierto. Una vez que el comerciante lo abre o cierra, no se lo cambiamos
// solos, salvo para mostrarle el resultado de una conexión.
let connectCardTouched = false;

function setConnectCardOpen(open) {
  const card = document.getElementById("connectCard");
  if (card) card.open = open;
}

function syncConnectCard(stores) {
  if (connectCardTouched) return;
  const message = document.getElementById("connectMessage");
  if (message && !message.hidden) return;
  setConnectCardOpen(!stores.length);
}

// Error con el número a mostrar: el estado HTTP de la respuesta. Un fallo de
// red no tiene respuesta, y un aviso de validación del navegador, tampoco.
function connectError(message, code = null) {
  const error = new Error(message);
  error.code = code;
  return error;
}

// ---------------------------------------------------------------------------
// Vuelta de la instalación
// ---------------------------------------------------------------------------

// Lee el resultado de la query string y la limpia enseguida: el token de
// vinculación no tiene que quedar en el historial ni repetirse al recargar.
function readInstallResult() {
  const params = new URLSearchParams(window.location.search);
  const result = {
    connected: params.get("store_connected"),
    claim: params.get("store_claim"),
    error: params.get("store_error"),
  };
  if (result.connected || result.claim || result.error) {
    window.history.replaceState(null, "", window.location.pathname);
  }
  return result;
}

function showInstallResult(result) {
  if (result.error) {
    showConnectResult(STORE_ERROR_MESSAGES[result.error] || "No se pudo conectar la tienda.", "error", result.error);
  } else if (result.connected) {
    showConnectResult(
      "¡Listo! Tu tienda quedó conectada. Estamos importando sus pedidos: pueden tardar unos minutos en aparecer.",
      "success"
    );
  }
}

async function claimStore(token) {
  try {
    const response = await apiFetch(`${STORES_URL}claim/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudo vincular la tienda a tu cuenta."));
    }
    showMessage(
      "La tienda quedó vinculada a tu cuenta. Estamos importando sus pedidos: pueden tardar unos minutos en aparecer.",
      "success"
    );
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo vincular la tienda a tu cuenta.");
  } finally {
    // Se descarta aunque falle: reintentar con el mismo token no lo arregla.
    localStorage.removeItem(PENDING_CLAIM_KEY);
  }
}

// ---------------------------------------------------------------------------
// Conectar / desconectar
// ---------------------------------------------------------------------------

// platform: "tiendanube" | "shopify". shop: dominio de la tienda, solo
// Shopify (el backend lo valida y lo normaliza).
async function connectStore(platform, { button, shop } = {}) {
  const label = PLATFORM_LABELS[platform] || platform;
  if (button) button.disabled = true;
  try {
    const query = shop ? `?${new URLSearchParams({ shop })}` : "";
    const response = await apiFetch(`${INTEGRATIONS_API}/${platform}/install-url/${query}`);
    const data = await response.json().catch(() => ({}));
    if (response.status === 503) {
      throw connectError(`La conexión con ${label} todavía no está configurada en el servidor.`, 503);
    }
    if (!response.ok || !data.authorize_url) {
      throw connectError(getErrorMessage(data, `No se pudo iniciar la conexión con ${label}.`), response.status);
    }
    window.location.assign(data.authorize_url);
  } catch (err) {
    if (err.isSessionExpired) return;
    showConnectResult(
      err.code ? err.message : "No pudimos comunicarnos con el servidor. Revisá tu conexión y probá de nuevo.",
      "error",
      err.code || null
    );
    if (button) button.disabled = false;
  }
}

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

async function disconnectStore(store, button) {
  const name = store.name || `la tienda ${store.external_store_id}`;
  const confirmed = window.confirm(
    `¿Desconectar ${name}? Sus pedidos nuevos van a dejar de entrar. ` +
      `Para desinstalar la app del todo, hacelo también desde el panel de ${store.platform_label || "tu tienda"}.`
  );
  if (!confirmed) return;

  button.disabled = true;
  try {
    const response = await apiFetch(`${STORES_URL}${store.id}/disconnect/`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudo desconectar la tienda."));
    }
    showMessage(`${name} quedó desconectada.`, "success");
    await loadStores();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo desconectar la tienda.");
    button.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Lista de tiendas
// ---------------------------------------------------------------------------

function addDetail(list, label, value) {
  list.appendChild(createElement("dt", "", label));
  list.appendChild(createElement("dd", "", value));
}

function renderStoreCard(store) {
  const card = createElement("article", "store-card");
  card.dataset.storeId = store.id;

  const head = createElement("div", "store-card-head");
  const title = createElement("div");
  title.appendChild(createElement("h3", "store-name", store.name || `Tienda ${store.external_store_id}`));
  title.appendChild(createElement("span", "store-platform", store.platform_label || store.platform));
  head.appendChild(title);
  head.appendChild(
    createElement("span", `store-status ${STATUS_CLASSES[store.status] || "off"}`, store.status_label || store.status)
  );
  card.appendChild(head);

  if (store.store_url && /^https?:\/\//i.test(store.store_url)) {
    const link = createElement("a", "store-link", store.store_url);
    link.href = store.store_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    card.appendChild(link);
  }

  const details = createElement("dl", "store-details");
  addDetail(details, "Conectada", formatDate(store.connected_at));
  addDetail(details, "Última sincronización", formatDate(store.last_synced_at));
  if (store.status === "revoked") addDetail(details, "Desconectada", formatDate(store.disconnected_at));
  card.appendChild(details);

  if (store.last_error) {
    card.appendChild(createElement("p", "store-error", store.last_error));
  }

  card.appendChild(renderSenderForm(store));
  if (store.platform === "woocommerce" && store.status === "active") {
    card.appendChild(renderPrintPlugin(store));
  }

  const actions = createElement("div", "store-actions");
  if (store.status !== "active") {
    const reconnect = createElement("button", "btn btn-primary btn-small", "Volver a conectar");
    reconnect.type = "button";
    // Shopify y WooCommerce reconectan la misma tienda: su dominio / sitio.
    const shop =
      store.platform === "shopify" ? store.external_store_id : store.platform === "woocommerce" ? store.store_url : "";
    reconnect.addEventListener("click", () => connectStore(store.platform, { button: reconnect, shop }));
    actions.appendChild(reconnect);
  }
  if (store.status !== "revoked") {
    const disconnect = createElement("button", "btn btn-outline btn-small", "Desconectar");
    disconnect.type = "button";
    disconnect.addEventListener("click", () => disconnectStore(store, disconnect));
    actions.appendChild(disconnect);
  }
  card.appendChild(actions);

  return card;
}

// ---------------------------------------------------------------------------
// Plugin de WordPress para imprimir desde WooCommerce
// ---------------------------------------------------------------------------

// El plugin agrega "Imprimir rótulos" a las acciones masivas de WooCommerce →
// Pedidos. El comerciante no lo configura: "Verificar plugin" le pide al
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
        ? "En WooCommerce → Pedidos tildá los pedidos y elegí “Imprimir rótulos” en Acciones masivas."
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

// ---------------------------------------------------------------------------
// Remitente de los rótulos de la tienda
// ---------------------------------------------------------------------------

// Lo que se imprime como "quién despacha" en los rótulos de ESA tienda
// (PATCH /integrations/stores/<id>/sender/). Cada tienda tiene el suyo: un
// mismo usuario puede tener varias y el remitente cambia con cada una.
const SENDER_FIELDS = [
  { key: "sender_name", label: "Nombre del remitente", placeholder: "Nombre que va en el rótulo" },
  { key: "sender_address", label: "Domicilio", placeholder: "Calle, número, ciudad" },
  { key: "sender_phone", label: "Teléfono", placeholder: "11 5555-5555" },
];

// Densidades que soporta el generador ZPL (apps.labels.zpl.SUPPORTED_DPMM),
// nombradas por lo que dice la etiqueta de la impresora y no por el dpmm,
// que nadie tiene por qué conocer.
const PRINTER_OPTIONS = [
  { value: "", label: "Sin configurar (203 dpi)" },
  { value: "8", label: "Zebra 203 dpi (ZD220, ZD230, GK420...)" },
  { value: "12", label: "Zebra 300 dpi (ZD620, ZT411...)" },
  { value: "6", label: "Zebra 152 dpi" },
  { value: "24", label: "Zebra 600 dpi" },
];

// Formularios "Rótulos de esta tienda" abiertos, por id de tienda: cada
// loadStores recrea las tarjetas, y uno abierto no tiene que cerrarse solo
// (por ejemplo, al guardar).
const openSenderForms = new Set();

function renderSenderForm(store) {
  // Desplegable: con varias tiendas, la lista no es una pila de formularios.
  const wrapper = createElement("details", "store-sender store-sender-collapsible");
  wrapper.open = openSenderForms.has(store.id);
  wrapper.addEventListener("toggle", () => {
    if (wrapper.open) openSenderForms.add(store.id);
    else openSenderForms.delete(store.id);
  });
  const summary = createElement("summary", "store-sender-summary");
  summary.appendChild(createElement("h4", "store-sender-title", "Rótulos de esta tienda"));
  wrapper.appendChild(summary);
  wrapper.appendChild(
    createElement(
      "p",
      "store-sender-help",
      "Remitente, logo, plantilla e impresora que se usan al imprimir los rótulos de esta " +
        "tienda. Si dejás el remitente vacío se usa el nombre de la tienda."
    )
  );

  const inputs = {};
  SENDER_FIELDS.forEach(({ key, label, placeholder }) => {
    const field = createElement("div", "field");
    const id = `${key}_${store.id}`;
    const labelEl = createElement("label", null, label);
    labelEl.setAttribute("for", id);
    const input = document.createElement("input");
    input.type = "text";
    input.id = id;
    input.placeholder = placeholder;
    input.value = store[key] || "";
    field.appendChild(labelEl);
    field.appendChild(input);
    wrapper.appendChild(field);
    inputs[key] = input;
  });

  // Plantilla preferida: se usa cuando se generan rótulos por lote de
  // pedidos de esta tienda sin elegir plantilla a mano.
  const templateField = createElement("div", "field");
  const templateLabel = createElement("label", null, "Plantilla preferida");
  templateLabel.setAttribute("for", `default_template_${store.id}`);
  const templateSelect = document.createElement("select");
  templateSelect.id = `default_template_${store.id}`;
  fillTemplateOptions(templateSelect, store.default_template);
  templateField.appendChild(templateLabel);
  templateField.appendChild(templateSelect);
  wrapper.appendChild(templateField);

  // Impresora térmica: la densidad decide con cuántos dots se dibuja el
  // rótulo (ver apps.labels.zpl). Sin elegir nada, el lote usa 203 dpi, que
  // es lo que tiene la enorme mayoría; esto es para el que tiene otra y no
  // quiere elegirla en cada impresión.
  const printerField = createElement("div", "field");
  const printerLabel = createElement("label", null, "Impresora térmica");
  printerLabel.setAttribute("for", `label_printer_dpmm_${store.id}`);
  const printerSelect = document.createElement("select");
  printerSelect.id = `label_printer_dpmm_${store.id}`;
  PRINTER_OPTIONS.forEach(({ value, label }) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    printerSelect.appendChild(option);
  });
  printerSelect.value = store.label_printer_dpmm ? String(store.label_printer_dpmm) : "";
  printerField.appendChild(printerLabel);
  printerField.appendChild(printerSelect);
  wrapper.appendChild(printerField);

  // Logo: archivo de imagen. Se manda como data URL, igual que el editor.
  const logoField = createElement("div", "field");
  const logoLabel = createElement("label", null, "Logo");
  logoLabel.setAttribute("for", `logo_${store.id}`);
  const logoInput = document.createElement("input");
  logoInput.type = "file";
  logoInput.id = `logo_${store.id}`;
  logoInput.accept = "image/*";
  logoField.appendChild(logoLabel);
  if (store.logo) {
    const current = createElement("div", "store-logo-current");
    const preview = document.createElement("img");
    preview.src = store.logo;
    preview.alt = "";
    const remove = createElement("button", "btn-danger-text", "Quitar logo");
    remove.type = "button";
    remove.addEventListener("click", () => saveSettings(store, { logo: null }, remove, feedback));
    current.appendChild(preview);
    current.appendChild(remove);
    logoField.appendChild(current);
  }
  logoField.appendChild(logoInput);
  wrapper.appendChild(logoField);

  const feedback = createElement("p", "store-sender-msg");
  feedback.hidden = true;
  wrapper.appendChild(feedback);

  const save = createElement("button", "btn btn-outline btn-small", "Guardar");
  save.type = "button";
  save.addEventListener("click", async () => {
    const payload = {};
    SENDER_FIELDS.forEach(({ key }) => {
      payload[key] = inputs[key].value.trim();
    });
    payload.default_template = templateSelect.value ? Number(templateSelect.value) : null;
    payload.label_printer_dpmm = printerSelect.value ? Number(printerSelect.value) : null;
    if (logoInput.files && logoInput.files[0]) {
      try {
        payload.logo = await readFileAsDataUrl(logoInput.files[0]);
      } catch {
        showFeedback(feedback, "No se pudo leer el archivo del logo.", false);
        return;
      }
    }
    saveSettings(store, payload, save, feedback);
  });
  wrapper.appendChild(save);

  return wrapper;
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

// Aviso dentro de una sección de la tarjeta de una tienda, después de
// redibujar la lista (las tarjetas se recrean en cada loadStores).
function showStoreFeedback(storeId, sectionSelector, text, ok) {
  const section = document.querySelector(`[data-store-id="${storeId}"] ${sectionSelector}`);
  if (section?.tagName === "DETAILS") section.open = true;
  const feedback = section?.querySelector(".store-sender-msg");
  if (!feedback) {
    showMessage(text, ok ? "success" : "error");
    return;
  }
  showFeedback(feedback, text, ok);
  feedback.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function showFeedback(feedback, text, ok) {
  feedback.textContent = text;
  feedback.className = `store-sender-msg ${ok ? "ok" : "error"}`;
  feedback.hidden = false;
}

// Plantillas disponibles (propias + públicas): se cargan una sola vez y se
// reusan en el select de cada tienda.
let templatesCache = [];

function fillTemplateOptions(select, selectedId) {
  select.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "Usar la plantilla por defecto";
  select.appendChild(empty);
  templatesCache.forEach((template) => {
    const option = document.createElement("option");
    option.value = template.id;
    option.textContent = template.name;
    if (template.id === selectedId) option.selected = true;
    select.appendChild(option);
  });
}

async function loadTemplates() {
  try {
    const response = await apiFetch(TEMPLATES_URL);
    if (!response.ok) return;
    const data = await response.json();
    templatesCache = Array.isArray(data) ? data : data.results || [];
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar plantillas:", err);
  }
}

async function saveSettings(store, payload, button, feedback) {
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Guardando...";
  feedback.hidden = true;

  try {
    const response = await apiFetch(`${STORES_URL}${store.id}/settings/`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudo guardar."));
    }
    // El logo puede haber cambiado (o borrado): se redibuja la lista con lo
    // que devolvió el backend, que es la fuente de verdad. El aviso va en la
    // tarjeta NUEVA de esa tienda: la anterior ya no está en la página.
    await loadStores();
    showStoreFeedback(store.id, ".store-sender:not(.store-plugin)", "Cambios guardados.", true);
    return;
  } catch (err) {
    if (err.isSessionExpired) return;
    showFeedback(feedback, err.message || "No se pudo guardar.", false);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

function renderStores(stores) {
  syncConnectCard(stores);
  const list = document.getElementById("storesList");
  list.replaceChildren();
  if (!stores.length) {
    list.appendChild(createElement("p", "muted-text", "Todavía no conectaste ninguna tienda."));
    return;
  }
  stores.forEach((store) => list.appendChild(renderStoreCard(store)));
}

async function loadStores() {
  const list = document.getElementById("storesList");
  try {
    const response = await apiFetch(STORES_URL);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudieron cargar tus tiendas."));
    }
    renderStores(Array.isArray(data) ? data : data.results || []);
  } catch (err) {
    if (err.isSessionExpired) return;
    list.replaceChildren(createElement("p", "muted-text", err.message || "No se pudieron cargar tus tiendas."));
  }
}

// ---------------------------------------------------------------------------
// Inicio
// ---------------------------------------------------------------------------

async function init() {
  const result = readInstallResult();

  if (!window.Auth.getAccessToken()) {
    // Instaló la app desde su plataforma sin haber iniciado sesión: se guarda
    // el enlace de vinculación y se retoma al volver del login.
    if (result.claim) localStorage.setItem(PENDING_CLAIM_KEY, result.claim);
    window.location.replace("index.html");
    return;
  }

  window.AppTopbar.render();
  const permissions = window.Auth.getCurrentUser()?.permissions || [];
  document.getElementById("apiConnectLink").hidden = !permissions.includes("integrations.manage");
  showInstallResult(result);

  const claimToken = result.claim || localStorage.getItem(PENDING_CLAIM_KEY);
  if (claimToken) await claimStore(claimToken);

  // Las plantillas primero: el select de cada tienda las necesita.
  await loadTemplates();
  await loadStores();
}

document
  .getElementById("connectTiendanubeBtn")
  ?.addEventListener("click", (event) => connectStore("tiendanube", { button: event.currentTarget }));
document.getElementById("connectShopifyForm")?.addEventListener("submit", connectShopify);
document.getElementById("connectWooForm")?.addEventListener("submit", connectWoo);
document.getElementById("wooManualForm")?.addEventListener("submit", connectWooManual);
document.getElementById("shopifyManualForm")?.addEventListener("submit", connectShopifyManual);
document.getElementById("refreshStoresBtn")?.addEventListener("click", loadStores);
// Se marca con el clic en el título y no con el evento "toggle": ese evento
// también salta cuando lo abre o cierra setConnectCardOpen.
document.querySelector("#connectCard > summary")?.addEventListener("click", () => {
  connectCardTouched = true;
});
document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

init();
