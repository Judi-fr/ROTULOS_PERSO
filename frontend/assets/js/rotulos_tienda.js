// Rótulos pedidos desde el admin de la tienda (rotulos_tienda.html).
//
// Pantalla de solo lectura: estos rótulos los pide la plataforma, no el
// comerciante, así que acá no se crea ni se borra nada. Sirve para una sola
// cosa — que el comerciante vea por qué NO le salió una etiqueta, en vez de
// que el motivo quede en el admin de Django.
//
// Sesión y apiFetch salen de assets/js/auth.js (window.Auth); escapeHtml,
// showMessage, formatDate y extractResults, de assets/js/utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const ME_URL = `${API_BASE}/auth/me/`;
const STORES_URL = `${API_BASE}/integrations/stores/`;
const STORE_LABELS_URL = `${API_BASE}/integrations/store-labels/`;

// Mientras haya rótulos pendientes se refresca solo: el que los genera es
// el worker, no esta pantalla.
const POLL_INTERVAL_MS = 5000;

const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

const listEl = document.getElementById("labelList");
const storeFilterEl = document.getElementById("storeFilter");
const statusFilterEl = document.getElementById("statusFilter");
const planWarningEl = document.getElementById("planWarning");
const pagerEl = document.getElementById("pager");
const pageInfoEl = document.getElementById("pageInfo");
const prevBtn = document.getElementById("prevBtn");
const nextBtn = document.getElementById("nextBtn");

let storeFilter = "";
let statusFilter = "";
let page = 1;
let hasNext = false;
let pollTimer = null;
let stores = [];

// ---------------------------------------------------------------------------
// Tiendas: alimentan el filtro y el aviso de plan.
// ---------------------------------------------------------------------------
function renderStoreOptions() {
  stores.forEach((store) => {
    const option = document.createElement("option");
    option.value = store.id;
    option.textContent = store.name || `Tienda ${store.external_store_id}`;
    storeFilterEl.appendChild(option);
  });
}

// label_api_enabled === false significa que el plan de esa tienda no
// incluye la API de rótulos. null es "no se sabe" (tienda conectada antes
// de que se guardara el dato) y no se avisa nada.
function renderPlanWarning() {
  const blocked = stores.filter((store) => store.label_api_enabled === false);
  if (!blocked.length) {
    planWarningEl.style.display = "none";
    return;
  }
  const names = blocked
    .map((store) => escapeHtml(store.name || store.external_store_id))
    .join(", ");
  planWarningEl.innerHTML = `
    El plan de ${names} no incluye la impresión de etiquetas desde el panel de
    la tienda, así que desde ahí no vas a poder pedirlas. Podés imprimir esos
    rótulos igual desde <a href="imprimir_rotulos.html">Imprimir rótulos</a>.
  `;
  planWarningEl.style.display = "block";
}

async function loadStores() {
  try {
    const response = await apiFetch(STORES_URL);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    stores = extractResults(await response.json());
    renderStoreOptions();
    renderPlanWarning();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar las tiendas:", err);
  }
}

// ---------------------------------------------------------------------------
// Lista de rótulos.
// ---------------------------------------------------------------------------
function statusBadge(labelRequest) {
  const labels = {
    pending: "Pendiente",
    ready: "Generado",
    failed: "Falló",
    canceled: "Cancelado",
    suspended: "Suspendido",
  };
  const text = labelRequest.status_label || labels[labelRequest.status] || labelRequest.status;
  return `<span class="status-badge ${escapeHtml(labelRequest.status)}"><span class="dot"></span>${escapeHtml(text)}</span>`;
}

// El nombre de la tienda lo escribe un tercero (el comerciante en su panel):
// nunca va crudo a innerHTML.
function buildItem(labelRequest) {
  const item = document.createElement("div");
  item.className = "label-item";

  const info = document.createElement("div");
  info.className = "label-item-info";
  const shipment = labelRequest.external_fulfillment_order_id
    ? `Envío ${escapeHtml(labelRequest.external_fulfillment_order_id)}`
    : "Envío sin identificar";
  info.innerHTML = `
    <p class="label-item-title">${shipment}</p>
    <p class="label-item-meta">${escapeHtml(labelRequest.store_name || "Tienda")} · etiqueta ${escapeHtml(labelRequest.external_label_id)}</p>
    <p class="label-item-meta">Pedido: ${formatDate(labelRequest.created_at)}</p>
    ${labelRequest.error_message ? `<p class="label-item-error">${escapeHtml(labelRequest.error_message)}</p>` : ""}
  `;

  const badge = document.createElement("div");
  badge.innerHTML = statusBadge(labelRequest);

  item.append(info, badge);
  return item;
}

function renderEmptyState() {
  if (storeFilter || statusFilter) {
    listEl.innerHTML =
      '<p class="empty-state">No hay rótulos que coincidan con el filtro.</p>';
    return;
  }
  listEl.innerHTML =
    '<p class="empty-state">Todavía no pediste ningún rótulo desde el panel de tu tienda.</p>';
}

function renderList(items) {
  if (!items.length) {
    renderEmptyState();
    return;
  }
  listEl.innerHTML = "";
  items.forEach((labelRequest) => listEl.appendChild(buildItem(labelRequest)));
}

function renderPager() {
  const showPager = hasNext || page > 1;
  pagerEl.style.display = showPager ? "flex" : "none";
  pageInfoEl.textContent = `Página ${page}`;
  prevBtn.disabled = page <= 1;
  nextBtn.disabled = !hasNext;
}

function buildQuery() {
  const params = {};
  if (storeFilter) params.store = storeFilter;
  if (statusFilter) params.status = statusFilter;
  if (page > 1) params.page = page;
  return new URLSearchParams(params).toString();
}

function scheduleAutoRefresh(items) {
  clearTimeout(pollTimer);
  if (!items.some((labelRequest) => labelRequest.status === "pending")) return;
  pollTimer = setTimeout(loadLabels, POLL_INTERVAL_MS);
}

async function loadLabels() {
  const query = buildQuery();
  try {
    const response = await apiFetch(`${STORE_LABELS_URL}${query ? `?${query}` : ""}`);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    const data = await response.json();
    const items = extractResults(data);
    hasNext = Boolean(data && data.next);
    renderList(items);
    renderPager();
    scheduleAutoRefresh(items);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar los rótulos de la tienda:", err);
    listEl.innerHTML = '<p class="empty-state">No se pudieron cargar los rótulos.</p>';
  }
}

// ---------------------------------------------------------------------------
// Filtros y paginado: siempre contra el backend.
// ---------------------------------------------------------------------------
storeFilterEl.addEventListener("change", (e) => {
  storeFilter = e.target.value;
  page = 1;
  loadLabels();
});

statusFilterEl.addEventListener("change", (e) => {
  statusFilter = e.target.value;
  page = 1;
  loadLabels();
});

document.getElementById("refreshBtn").addEventListener("click", () => loadLabels());

prevBtn.addEventListener("click", () => {
  if (page <= 1) return;
  page -= 1;
  loadLabels();
});

nextBtn.addEventListener("click", () => {
  if (!hasNext) return;
  page += 1;
  loadLabels();
});

const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) {
  logoutBtn.addEventListener("click", () => window.Auth.logout());
}

async function init() {
  try {
    const response = await apiFetch(ME_URL);
    if (response.ok) {
      window.AppTopbar.render(await response.json());
    }
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar el perfil:", err);
  }
  await loadStores();
  await loadLabels();
}

init();
