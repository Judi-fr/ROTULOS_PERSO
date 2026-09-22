// Tiendas conectadas (backend: apps.integrations).
//
// - Conectar Tiendanube: pide la URL de autorización y lleva al comerciante
//   a Tiendanube.
// - Vuelta de la instalación: el backend redirige a esta página con
//   ?store_connected=<id>, ?store_claim=<token> o ?store_error=<código>
//   (ver TiendanubeCallbackView). Un store_claim se canjea para vincular la
//   tienda a la cuenta; si no hay sesión, se guarda y se retoma después del
//   login (index.html vuelve acá cuando existe pendingStoreClaim).
// - Lista de tiendas con su estado, errores y la acción de desconectar.
//
// Sesión y apiFetch salen de assets/js/auth.js (window.Auth). Todo texto
// que viene del backend (nombre, errores) se inserta con textContent.

const INTEGRATIONS_API = `${window.APP_CONFIG.API_BASE}/integrations`;
const STORES_URL = `${INTEGRATIONS_API}/stores/`;
const TEMPLATES_URL = `${window.APP_CONFIG.API_BASE}/labels/templates/`;
const INSTALL_URL = `${INTEGRATIONS_API}/tiendanube/install-url/`;
const PENDING_CLAIM_KEY = "pendingStoreClaim";

const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

const STORE_ERROR_MESSAGES = {
  missing_code: "Tiendanube no devolvió el código de autorización. Probá conectar la tienda de nuevo.",
  authorization_cancelled: "Se canceló la autorización en Tiendanube: la tienda no se conectó.",
  invalid_state: "El enlace de instalación venció o no es válido. Tocá “Conectar Tiendanube” otra vez.",
  authorization_rejected: "Tiendanube rechazó la autorización (el código venció o ya se usó). Probá de nuevo.",
  provider_unavailable: "No pudimos comunicarnos con Tiendanube. Probá de nuevo en unos minutos.",
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

// ---------------------------------------------------------------------------
// Vuelta de la instalación en Tiendanube
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
    showMessage(STORE_ERROR_MESSAGES[result.error] || "No se pudo conectar la tienda.");
  } else if (result.connected) {
    showMessage(
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

async function connectTiendanube(event) {
  const button = event?.currentTarget;
  if (button) button.disabled = true;
  try {
    const response = await apiFetch(INSTALL_URL);
    const data = await response.json().catch(() => ({}));
    if (response.status === 503) {
      throw new Error("La conexión con Tiendanube todavía no está configurada en el servidor.");
    }
    if (!response.ok || !data.authorize_url) {
      throw new Error(getErrorMessage(data, "No se pudo iniciar la conexión con Tiendanube."));
    }
    window.location.assign(data.authorize_url);
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo iniciar la conexión con Tiendanube.");
    if (button) button.disabled = false;
  }
}

async function disconnectStore(store, button) {
  const name = store.name || `la tienda ${store.external_store_id}`;
  const confirmed = window.confirm(
    `¿Desconectar ${name}? Sus pedidos nuevos van a dejar de entrar. ` +
      "Para desinstalar la app del todo, hacelo también desde el panel de Tiendanube."
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

  const actions = createElement("div", "store-actions");
  if (store.status !== "active") {
    const reconnect = createElement("button", "btn btn-primary btn-small", "Volver a conectar");
    reconnect.type = "button";
    reconnect.addEventListener("click", connectTiendanube);
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

function renderSenderForm(store) {
  const wrapper = createElement("div", "store-sender");
  wrapper.appendChild(createElement("h4", "store-sender-title", "Rótulos de esta tienda"));
  wrapper.appendChild(
    createElement(
      "p",
      "store-sender-help",
      "Remitente, logo y plantilla que se usan al imprimir los rótulos de esta tienda. " +
        "Si dejás el remitente vacío se usa el nombre de la tienda."
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
    // que devolvió el backend, que es la fuente de verdad.
    await loadStores();
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
    // Instaló la app desde Tiendanube sin haber iniciado sesión: se guarda
    // el enlace de vinculación y se retoma al volver del login.
    if (result.claim) localStorage.setItem(PENDING_CLAIM_KEY, result.claim);
    window.location.replace("index.html");
    return;
  }

  window.AppTopbar.render();
  showInstallResult(result);

  const claimToken = result.claim || localStorage.getItem(PENDING_CLAIM_KEY);
  if (claimToken) await claimStore(claimToken);

  // Las plantillas primero: el select de cada tienda las necesita.
  await loadTemplates();
  await loadStores();
}

document.getElementById("connectTiendanubeBtn")?.addEventListener("click", connectTiendanube);
document.getElementById("refreshStoresBtn")?.addEventListener("click", loadStores);
document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

init();
