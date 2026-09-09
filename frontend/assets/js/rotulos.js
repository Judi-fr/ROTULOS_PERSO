// Mis rótulos (rotulos.html): listado propio de apps.labels, buscador
// contra el backend, vista previa en modal y acciones Editar/Duplicar/
// Eliminar. El CRUD de rótulos vive en una única fuente de verdad —
// frontend/pedidos/api.js — que se importa dinámicamente porque este
// script es clásico (no type="module"), igual que exige el resto de la
// página. Sesión y logout salen de assets/js/auth.js (window.Auth),
// compartido con el resto del frontend.

const EDITOR_URL = "pedidos/diseñorotulos.html";
const SEARCH_DEBOUNCE_MS = 300;

const API_BASE = window.APP_CONFIG.API_BASE;
const TEMPLATES_URL = `${API_BASE}/labels/templates/`;
const ORDERS_URL = `${API_BASE}/orders/`;
const BATCH_URL = `${API_BASE}/labels/batch/`;

let labelsApi = null; // frontend/pedidos/api.js, cargado en init()
let searchTerm = "";
let searchDebounceTimer = null;

const getAccessToken = () => window.Auth.getAccessToken();
const getCurrentUser = () => window.Auth.getCurrentUser();
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

function showMessage(text, type = "error") {
  const el = document.getElementById("pageMessage");
  if (!el) return;
  el.textContent = text;
  el.className = `page-message ${type}`;
  el.style.display = "block";
}

function formatDate(iso) {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleDateString("es-AR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "-";
  }
}

function renderTopbar() {
  const user = getCurrentUser();
  const nameEl = document.getElementById("userName");
  const emailEl = document.getElementById("userEmail");
  const avatarEl = document.getElementById("userAvatar");
  const email = user.email || "";
  const name = [user.first_name, user.last_name].filter(Boolean).join(" ") || email || "Usuario";

  if (nameEl) nameEl.textContent = name;
  if (emailEl) emailEl.textContent = email || "—";
  if (avatarEl) {
    avatarEl.src = user.picture
      ? user.picture
      : `https://api.dicebear.com/7.x/avataaars/svg?seed=${encodeURIComponent(email || "user")}`;
  }
}

// ---------------------------------------------------------------------------
// Modal de vista previa: uno solo, reutilizado para cualquier tarjeta.
// ---------------------------------------------------------------------------
const previewOverlay = document.getElementById("previewOverlay");
const previewImg = document.getElementById("previewImg");
const previewNombre = document.getElementById("previewNombre");
const previewCliente = document.getElementById("previewCliente");
const previewFecha = document.getElementById("previewFecha");

function openPreview(rotulo) {
  previewImg.src = rotulo.thumbnail || "";
  previewImg.alt = `Vista previa de ${rotulo.nombre || "rótulo"}`;
  previewNombre.textContent = rotulo.nombre || "Rótulo sin nombre";
  previewCliente.textContent = rotulo.cliente || "-";
  previewFecha.textContent = formatDate(rotulo.updatedAt);
  previewOverlay.classList.add("open");
}

function closePreview() {
  previewOverlay.classList.remove("open");
}

document.getElementById("previewCloseBtn")?.addEventListener("click", closePreview);
previewOverlay?.addEventListener("click", (e) => {
  if (e.target === previewOverlay) closePreview();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && previewOverlay?.classList.contains("open")) closePreview();
});

// ---------------------------------------------------------------------------
// Grilla de rótulos.
// ---------------------------------------------------------------------------
const gridEl = document.getElementById("rotulosGrid");

function renderEmptyState() {
  if (searchTerm) {
    gridEl.innerHTML =
      '<div class="empty-state-block"><p>No hay rótulos que coincidan con tu búsqueda.</p></div>';
    return;
  }
  gridEl.innerHTML = `
    <div class="empty-state-block">
      <p>Todavía no creaste ningún rótulo.</p>
      <a class="btn btn-primary" href="${EDITOR_URL}">Crear mi primer rótulo</a>
    </div>
  `;
}

function buildCard(rotulo) {
  const card = document.createElement("div");
  card.className = "rotulo-card";

  const thumbBtn = document.createElement("button");
  thumbBtn.type = "button";
  thumbBtn.className = "rotulo-thumb";
  if (rotulo.thumbnail) {
    const img = document.createElement("img");
    img.src = rotulo.thumbnail;
    img.alt = rotulo.nombre || "Rótulo";
    thumbBtn.appendChild(img);
  } else {
    thumbBtn.classList.add("rotulo-thumb-empty");
    thumbBtn.textContent = "Sin vista previa";
  }
  thumbBtn.addEventListener("click", () => openPreview(rotulo));

  const body = document.createElement("div");
  body.className = "rotulo-card-body";
  const sizeText = rotulo.size ? `${rotulo.size.widthCm} × ${rotulo.size.heightCm} cm` : "-";
  const pedidoText = rotulo.order ? `Pedido #${rotulo.order}` : "Sin pedido asociado";
  body.innerHTML = `
    <h3 class="rotulo-card-title">${rotulo.nombre || "Rótulo sin nombre"}</h3>
    <p class="rotulo-card-meta">${rotulo.cliente || "Sin destinatario"}</p>
    <p class="rotulo-card-meta">${sizeText} · ${pedidoText}</p>
    <p class="rotulo-card-meta">Modificado: ${formatDate(rotulo.updatedAt)}</p>
  `;

  const actions = document.createElement("div");
  actions.className = "rotulo-card-actions";

  const editBtn = document.createElement("a");
  editBtn.className = "btn btn-outline";
  editBtn.textContent = "Editar";
  editBtn.href = `${EDITOR_URL}?id=${rotulo.id}`;

  const duplicateBtn = document.createElement("button");
  duplicateBtn.type = "button";
  duplicateBtn.className = "btn btn-outline";
  duplicateBtn.textContent = "Duplicar";
  duplicateBtn.addEventListener("click", () => handleDuplicate(rotulo));

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "btn btn-danger";
  deleteBtn.textContent = "Eliminar";
  deleteBtn.addEventListener("click", () => handleDelete(rotulo));

  actions.append(editBtn, duplicateBtn, deleteBtn);
  card.append(thumbBtn, body, actions);
  return card;
}

function renderGrid(rotulos) {
  if (!rotulos.length) {
    renderEmptyState();
    return;
  }
  gridEl.innerHTML = "";
  rotulos.forEach((rotulo) => gridEl.appendChild(buildCard(rotulo)));
}

async function loadRotulos() {
  gridEl.innerHTML = '<p class="empty-state">Cargando rótulos...</p>';
  try {
    const rotulos = await labelsApi.listRotulos(searchTerm ? { search: searchTerm } : {});
    renderGrid(rotulos);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar los rótulos:", err);
    gridEl.innerHTML = '<p class="empty-state">No se pudieron cargar tus rótulos.</p>';
  }
}

async function handleDuplicate(rotulo) {
  try {
    await labelsApi.duplicateRotulo(rotulo.id);
    showMessage(`Se duplicó "${rotulo.nombre || "el rótulo"}".`, "success");
    await loadRotulos();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al duplicar el rótulo:", err);
    showMessage(err.message || "No se pudo duplicar el rótulo.");
  }
}

async function handleDelete(rotulo) {
  const confirmed = window.confirm(`¿Eliminar "${rotulo.nombre || "este rótulo"}"?`);
  if (!confirmed) return;
  try {
    await labelsApi.deleteRotulo(rotulo.id);
    showMessage("Rótulo eliminado.", "success");
    await loadRotulos();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al eliminar el rótulo:", err);
    showMessage(err.message || "No se pudo eliminar el rótulo.");
  }
}

// ---------------------------------------------------------------------------
// Búsqueda: siempre contra el backend (?search=), con un debounce corto.
// ---------------------------------------------------------------------------
document.getElementById("searchInput")?.addEventListener("input", (e) => {
  clearTimeout(searchDebounceTimer);
  const value = e.target.value.trim();
  searchDebounceTimer = setTimeout(() => {
    searchTerm = value;
    loadRotulos();
  }, SEARCH_DEBOUNCE_MS);
});

// ---------------------------------------------------------------------------
// Generar por lote: plantilla + selección de pedidos (por id o por
// rango de fechas/estado) -> POST /api/v1/labels/batch/. Con el 202 en
// mano, redirige a documentos.html para seguir el progreso ahí (el lote
// corre síncrono, pero el documento ya existe con id propio).
// ---------------------------------------------------------------------------

function getErrorMessage(data, fallback) {
  if (typeof data === "string") return data;
  if (!data || typeof data !== "object") return fallback;
  if (typeof data.detail === "string") return data.detail;
  for (const value of Object.values(data)) {
    if (Array.isArray(value) && value.length) return String(value[0]);
    if (typeof value === "string") return value;
  }
  return fallback;
}

// La API pagina (PageNumberPagination): {count, next, previous, results}.
function extractResults(data) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.results)) return data.results;
  return [];
}

const batchPanel = document.getElementById("batchPanel");
const batchTemplateSelect = document.getElementById("batchTemplateSelect");
const batchOrdersField = document.getElementById("batchOrdersField");
const batchOrdersList = document.getElementById("batchOrdersList");
const batchFiltersField = document.getElementById("batchFiltersField");
const batchMsg = document.getElementById("batchMsg");
let batchOrdersLoaded = false;

function showBatchMsg(text, ok) {
  if (!batchMsg) return;
  batchMsg.textContent = text;
  batchMsg.style.color = ok ? "#16a34a" : "#dc2626";
  batchMsg.style.display = "block";
}

async function loadBatchTemplates() {
  batchTemplateSelect.innerHTML = '<option value="">Cargando plantillas...</option>';
  try {
    const response = await apiFetch(TEMPLATES_URL);
    if (!response.ok) throw new Error();
    const templates = extractResults(await response.json());
    if (!templates.length) {
      batchTemplateSelect.innerHTML = '<option value="">No hay plantillas disponibles</option>';
      return;
    }
    batchTemplateSelect.innerHTML = templates
      .map((t) => `<option value="${t.id}">${t.name}</option>`)
      .join("");
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar plantillas:", err);
    batchTemplateSelect.innerHTML = '<option value="">No se pudieron cargar las plantillas</option>';
  }
}

async function loadBatchOrders() {
  batchOrdersList.innerHTML = '<p class="empty-state">Cargando pedidos...</p>';
  try {
    const response = await apiFetch(ORDERS_URL);
    if (!response.ok) throw new Error();
    const orders = extractResults(await response.json());
    if (!orders.length) {
      batchOrdersList.innerHTML = '<p class="empty-state">Todavía no tenés pedidos.</p>';
      return;
    }
    batchOrdersList.innerHTML = orders
      .map(
        (order) => `
        <label class="batch-order-option">
          <input type="checkbox" value="${order.id}" />
          Pedido #${order.id} — ${order.status_label || order.status} —
          ${formatDate(order.created_at)}
        </label>
      `
      )
      .join("");
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar pedidos:", err);
    batchOrdersList.innerHTML = '<p class="empty-state">No se pudieron cargar tus pedidos.</p>';
  }
}

function openBatchPanel() {
  batchPanel.classList.add("open");
  batchPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  if (!batchOrdersLoaded) {
    batchOrdersLoaded = true;
    loadBatchTemplates();
    loadBatchOrders();
  }
}

function closeBatchPanel() {
  batchPanel.classList.remove("open");
}

document.getElementById("openBatchPanelBtn")?.addEventListener("click", openBatchPanel);
document.getElementById("closeBatchPanelBtn")?.addEventListener("click", closeBatchPanel);

document.querySelectorAll('input[name="batchMode"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    const byOrders = document.getElementById("batchModeOrders").checked;
    batchOrdersField.style.display = byOrders ? "block" : "none";
    batchFiltersField.style.display = byOrders ? "none" : "block";
  });
});

async function generateBatch() {
  const templateId = batchTemplateSelect.value;
  if (!templateId) {
    showBatchMsg("Elegí una plantilla.", false);
    return;
  }

  const output = document.getElementById("batchOutputSelect").value;
  const byOrders = document.getElementById("batchModeOrders").checked;
  const payload = { template_id: Number(templateId), output };

  if (byOrders) {
    const orderIds = Array.from(
      batchOrdersList.querySelectorAll('input[type="checkbox"]:checked')
    ).map((el) => Number(el.value));
    if (!orderIds.length) {
      showBatchMsg("Elegí al menos un pedido.", false);
      return;
    }
    payload.order_ids = orderIds;
  } else {
    const dateFrom = document.getElementById("batchDateFrom").value;
    const dateTo = document.getElementById("batchDateTo").value;
    const statusValue = document.getElementById("batchStatusSelect").value;
    const filters = {};
    if (dateFrom) filters.date_from = dateFrom;
    if (dateTo) filters.date_to = dateTo;
    if (statusValue) filters.status = statusValue;
    payload.filters = filters;
  }

  const button = document.getElementById("generateBatchBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Generando...";

  try {
    const response = await apiFetch(BATCH_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudo generar el lote."));
    }
    window.location.href = "documentos.html";
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al generar el lote:", err);
    showBatchMsg(err.message || "No se pudo generar el lote.", false);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

document.getElementById("generateBatchBtn")?.addEventListener("click", generateBatch);

// ---------------------------------------------------------------------------
// Logout: misma lógica que dashboard.js/perfil.js/pedidos.js (window.Auth.logout).
// ---------------------------------------------------------------------------
const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) {
  logoutBtn.addEventListener("click", () => window.Auth.logout());
}

async function init() {
  // frontend/pedidos/api.js es la única fuente de verdad del CRUD de
  // rótulos (import dinámico: este script no es un módulo).
  labelsApi = await import("../../pedidos/api.js");
  renderTopbar();
  await loadRotulos();
}

if (!getAccessToken()) {
  window.location.replace("index.html");
} else {
  init();
}
