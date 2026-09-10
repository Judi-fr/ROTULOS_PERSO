// Mis documentos (documentos.html): listado propio de apps.documents,
// buscador + filtro de estado contra el backend, descarga (blob, porque
// el endpoint exige el Bearer y un <a href> plano no puede mandarlo),
// eliminar con confirmación y refresco automático de los documentos
// "processing" hasta que cambien de estado. Sesión y apiFetch salen de
// assets/js/auth.js (window.Auth), igual que el resto del frontend.

const API_BASE = window.APP_CONFIG.API_BASE;
const ME_URL = `${API_BASE}/auth/me/`;
const DOCUMENTS_URL = `${API_BASE}/documents/`;

const POLL_INTERVAL_MS = 4000;
const SEARCH_DEBOUNCE_MS = 300;

const apiFetch = (url, options) => window.Auth.apiFetch(url, options);
const getAccessToken = () => window.Auth.getAccessToken();
const getCurrentUser = () => window.Auth.getCurrentUser();

let searchTerm = "";
let statusFilter = "";
let searchDebounceTimer = null;
let pollTimer = null;

function showMessage(text, type = "error") {
  const el = document.getElementById("pageMessage");
  if (!el) return;
  el.textContent = text;
  el.className = `page-message ${type}`;
  el.style.display = "block";
}

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
    return iso;
  }
}

function formatSize(bytes) {
  if (!bytes) return "-";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
}

function renderTopbar(user) {
  const nameEl = document.getElementById("userName");
  const emailEl = document.getElementById("userEmail");
  const avatarEl = document.getElementById("userAvatar");
  const email = user.email || "";
  const name = [user.first_name, user.last_name].filter(Boolean).join(" ") || email || "Usuario";

  if (nameEl) nameEl.textContent = name;
  if (emailEl) emailEl.textContent = email || "—";
  if (avatarEl) {
    const picture = getCurrentUser().picture;
    avatarEl.src = picture
      ? picture
      : `https://api.dicebear.com/7.x/avataaars/svg?seed=${encodeURIComponent(email || "user")}`;
  }
}

// ---------------------------------------------------------------------------
// Descarga: blob + object URL, porque el endpoint exige Authorization y un
// <a href> plano no puede mandar ese header (mismo criterio que el PDF del
// servidor en pedidos/diseñorotulos.html).
// ---------------------------------------------------------------------------
// Un ZIP no se imprime (son varios PDFs sueltos, ver isPrintablePdf): ahí
// solo tiene sentido "Descargar".
function isPrintablePdf(doc) {
  return doc.status === "ready" && (doc.file_name || "").toLowerCase().endsWith(".pdf");
}

async function fetchDocumentBlob(doc) {
  const response = await apiFetch(`${DOCUMENTS_URL}${doc.id}/download/`);
  if (!response.ok) {
    throw new Error("No se pudo generar el archivo para imprimir.");
  }
  return response.blob();
}

async function downloadDocument(doc) {
  try {
    const response = await apiFetch(`${DOCUMENTS_URL}${doc.id}/download/`);
    if (!response.ok) {
      throw new Error("No se pudo descargar el archivo.");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = doc.file_name || `${doc.name}`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al descargar el documento:", err);
    showMessage(err.message || "No se pudo descargar el documento.");
  }
}

async function deleteDocument(doc) {
  const confirmed = window.confirm(`¿Eliminar "${doc.name}"?`);
  if (!confirmed) return;
  try {
    const response = await apiFetch(`${DOCUMENTS_URL}${doc.id}/`, { method: "DELETE" });
    if (!response.ok && response.status !== 204) {
      const data = await response.json().catch(() => ({}));
      throw new Error(getErrorMessage(data, "No se pudo eliminar el documento."));
    }
    showMessage("Documento eliminado.", "success");
    await loadDocuments();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al eliminar el documento:", err);
    showMessage(err.message || "No se pudo eliminar el documento.");
  }
}

// ---------------------------------------------------------------------------
// Lista de documentos.
// ---------------------------------------------------------------------------
const listEl = document.getElementById("documentList");

function renderEmptyState() {
  if (searchTerm || statusFilter) {
    listEl.innerHTML =
      '<p class="empty-state">No hay documentos que coincidan con la búsqueda/filtro.</p>';
    return;
  }
  listEl.innerHTML =
    '<p class="empty-state">Todavía no generaste ningún documento. Probá "Generar por lote" desde Mis rótulos.</p>';
}

function statusBadge(doc) {
  const labels = { processing: "Procesando", ready: "Listo", failed: "Falló" };
  const label = doc.status_label || labels[doc.status] || doc.status;
  return `<span class="status-badge ${doc.status}"><span class="dot"></span>${label}</span>`;
}

function buildItem(doc) {
  const item = document.createElement("div");
  item.className = "document-item";

  const info = document.createElement("div");
  info.className = "document-item-info";
  info.innerHTML = `
    <p class="document-item-title">${doc.name}</p>
    <p class="document-item-meta">${doc.kind_label || doc.kind} · ${doc.item_count} rótulo(s) · ${formatSize(doc.size_bytes)}</p>
    <p class="document-item-meta">Creado: ${formatDate(doc.created_at)}</p>
    ${doc.status === "failed" && doc.error_message ? `<p class="document-item-error">${doc.error_message}</p>` : ""}
    ${doc.status === "ready" && doc.error_message ? `<p class="document-item-meta">${doc.error_message}</p>` : ""}
  `;

  const actions = document.createElement("div");
  actions.className = "document-item-actions";
  actions.innerHTML = statusBadge(doc);

  if (doc.status === "ready") {
    const downloadBtn = document.createElement("button");
    downloadBtn.type = "button";
    downloadBtn.className = "btn btn-outline btn-small";
    downloadBtn.textContent = "Descargar";
    downloadBtn.addEventListener("click", () => downloadDocument(doc));
    actions.appendChild(downloadBtn);

    if (isPrintablePdf(doc)) {
      const printBtn = document.createElement("button");
      printBtn.type = "button";
      printBtn.className = "btn btn-outline btn-small";
      printBtn.textContent = "Imprimir";
      printBtn.addEventListener("click", () =>
        window.PrintHelper.printFromBlobFn(() => fetchDocumentBlob(doc), printBtn)
      );
      actions.appendChild(printBtn);
    }
  }

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "btn-danger-text";
  deleteBtn.textContent = "Eliminar";
  deleteBtn.addEventListener("click", () => deleteDocument(doc));
  actions.appendChild(deleteBtn);

  item.append(info, actions);
  return item;
}

function renderList(documents) {
  if (!documents.length) {
    renderEmptyState();
    return;
  }
  listEl.innerHTML = "";
  documents.forEach((doc) => listEl.appendChild(buildItem(doc)));
}

function buildQuery() {
  const params = {};
  if (searchTerm) params.search = searchTerm;
  if (statusFilter) params.status = statusFilter;
  return new URLSearchParams(params).toString();
}

// Mientras haya al menos un documento "processing" en la página actual,
// se refresca solo hasta que todos cambien de estado — así el usuario ve
// el "listo"/"falló" sin tener que recargar la pantalla a mano.
function scheduleAutoRefresh(documents) {
  clearTimeout(pollTimer);
  const stillProcessing = documents.some((doc) => doc.status === "processing");
  if (!stillProcessing) return;
  pollTimer = setTimeout(loadDocuments, POLL_INTERVAL_MS);
}

async function loadDocuments() {
  const query = buildQuery();
  try {
    const response = await apiFetch(`${DOCUMENTS_URL}${query ? `?${query}` : ""}`);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    const data = await response.json();
    const documents = extractResults(data);
    renderList(documents);
    scheduleAutoRefresh(documents);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar los documentos:", err);
    listEl.innerHTML = '<p class="empty-state">No se pudieron cargar tus documentos.</p>';
  }
}

// ---------------------------------------------------------------------------
// Búsqueda y filtro: siempre contra el backend.
// ---------------------------------------------------------------------------
document.getElementById("searchInput")?.addEventListener("input", (e) => {
  clearTimeout(searchDebounceTimer);
  const value = e.target.value.trim();
  searchDebounceTimer = setTimeout(() => {
    searchTerm = value;
    loadDocuments();
  }, SEARCH_DEBOUNCE_MS);
});

document.getElementById("statusFilter")?.addEventListener("change", (e) => {
  statusFilter = e.target.value;
  loadDocuments();
});

// ---------------------------------------------------------------------------
// Logout: misma lógica que pedidos.js/rotulos.js (window.Auth.logout).
// ---------------------------------------------------------------------------
const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) {
  logoutBtn.addEventListener("click", () => window.Auth.logout());
}

async function init() {
  try {
    const response = await apiFetch(ME_URL);
    if (response.ok) {
      renderTopbar(await response.json());
    }
  } catch (err) {
    if (err.isSessionExpired) return;
  }
  await loadDocuments();
}

if (!getAccessToken()) {
  window.location.replace("index.html");
} else {
  init();
}
