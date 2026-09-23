// Mis rótulos (mis_rotulos.html): listado propio de apps.labels, buscador
// contra el backend, vista previa en modal y acciones Editar/Duplicar/
// Eliminar. El CRUD de rótulos vive en una única fuente de verdad —
// frontend/assets/js/labels_api.js — que se importa dinámicamente porque
// este script es clásico (no type="module"), igual que exige el resto de la
// página. Sesión y logout salen de assets/js/auth.js (window.Auth),
// compartido con el resto del frontend.

const EDITOR_URL = "editor_rotulos.html";
const SEARCH_DEBOUNCE_MS = 300;

const API_BASE = window.APP_CONFIG.API_BASE;

let labelsApi = null; // frontend/assets/js/labels_api.js, cargado en init()
let searchTerm = "";
let searchDebounceTimer = null;

const getAccessToken = () => window.Auth.getAccessToken();
const getCurrentUser = () => window.Auth.getCurrentUser();
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);


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
    <h3 class="rotulo-card-title">${escapeHtml(rotulo.nombre || "Rótulo sin nombre")}</h3>
    <p class="rotulo-card-meta">${escapeHtml(rotulo.cliente || "Sin destinatario")}</p>
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

  const printBtn = document.createElement("button");
  printBtn.type = "button";
  printBtn.className = "btn btn-outline";
  printBtn.textContent = "Imprimir";
  printBtn.addEventListener("click", () =>
    window.PrintHelper.printFromBlobFn(() => labelsApi.downloadRotuloPdfServer(rotulo.id), printBtn)
  );

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "btn btn-danger";
  deleteBtn.textContent = "Eliminar";
  deleteBtn.addEventListener("click", () => handleDelete(rotulo));

  actions.append(editBtn, duplicateBtn, printBtn, deleteBtn);
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
// Logout: misma lógica que dashboard.js/perfil.js/pedidos.js (window.Auth.logout).
// ---------------------------------------------------------------------------
const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) {
  logoutBtn.addEventListener("click", () => window.Auth.logout());
}

async function init() {
  // frontend/assets/js/labels_api.js es la única fuente de verdad del CRUD
  // de rótulos (import dinámico: este script no es un módulo).
  labelsApi = await import("./labels_api.js");
  window.AppTopbar.render();
  await loadRotulos();
}

if (!getAccessToken()) {
  window.location.replace("index.html");
} else {
  init();
}
