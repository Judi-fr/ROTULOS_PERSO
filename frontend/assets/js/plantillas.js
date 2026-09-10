// Mis plantillas (plantillas.html): listado de apps.labels.LabelTemplate
// (públicas + propias activas), buscador contra el backend, vista previa
// real (PDF del endpoint de render, o el campo `preview` si ya está
// cargado) y acciones Usar/Duplicar/Editar/Archivar. El CRUD de plantillas
// vive en frontend/pedidos/api.js (única fuente de verdad, compartida con
// el editor): import dinámico porque este script es clásico, igual patrón
// que assets/js/rotulos.js. Sesión y logout salen de assets/js/auth.js
// (window.Auth).

const EDITOR_URL = "pedidos/diseñorotulos.html";
const SEARCH_DEBOUNCE_MS = 300;

let templatesApi = null; // frontend/pedidos/api.js, cargado en init()
let searchTerm = "";
let searchDebounceTimer = null;
let currentPreviewUrl = null; // object URL del PDF de vista previa en pantalla

const getAccessToken = () => window.Auth.getAccessToken();
const getCurrentUser = () => window.Auth.getCurrentUser();

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

// Solo lo decide el backend en cada request real (esto es UI-only, igual
// criterio que isAdminMode()/canUseUserPermission() en admingestion_test.js):
// una plantilla PROPIA y privada se puede editar/archivar con
// labels.template_create; una pública o de otro usuario necesita
// labels.manage_templates.
function hasPermission(permission) {
  const permissions = getCurrentUser().permissions;
  return Array.isArray(permissions) && permissions.includes(permission);
}

function canManageTemplate(template) {
  const user = getCurrentUser();
  const isOwnPrivate = template.owner === user.id && !template.isPublic;
  return hasPermission("labels.manage_templates") || isOwnPrivate;
}

// ---------------------------------------------------------------------------
// Modal de vista previa: PDF real (o el campo `preview` si ya está
// cargado), no una miniatura genérica.
// ---------------------------------------------------------------------------
const previewOverlay = document.getElementById("previewOverlay");
const previewImg = document.getElementById("previewImg");
const previewFrame = document.getElementById("previewFrame");
const previewLoading = document.getElementById("previewLoading");
const previewNombre = document.getElementById("previewNombre");
const previewMeta = document.getElementById("previewMeta");

function releaseCurrentPreviewUrl() {
  if (currentPreviewUrl) {
    URL.revokeObjectURL(currentPreviewUrl);
    currentPreviewUrl = null;
  }
}

async function openPreview(template) {
  previewNombre.textContent = template.nombre || "Plantilla sin nombre";
  previewMeta.textContent =
    `${template.size.widthCm} × ${template.size.heightCm} cm · ` +
    (template.isPublic ? "Pública" : "Propia");
  previewImg.hidden = true;
  previewFrame.hidden = true;
  previewLoading.style.display = "block";
  previewOverlay.classList.add("open");
  releaseCurrentPreviewUrl();

  if (template.preview) {
    previewImg.src = template.preview;
    previewImg.hidden = false;
    previewLoading.style.display = "none";
    return;
  }

  try {
    const blob = await templatesApi.fetchTemplatePreviewPdf(template.id);
    currentPreviewUrl = URL.createObjectURL(blob);
    previewFrame.src = currentPreviewUrl;
    previewFrame.hidden = false;
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al generar la vista previa:", err);
    previewLoading.textContent = "No se pudo generar la vista previa.";
    return;
  } finally {
    previewLoading.style.display = "none";
  }
}

function closePreview() {
  previewOverlay.classList.remove("open");
  previewFrame.src = "";
  previewImg.src = "";
  previewLoading.textContent = "Generando vista previa...";
  releaseCurrentPreviewUrl();
}

document.getElementById("previewCloseBtn")?.addEventListener("click", closePreview);
previewOverlay?.addEventListener("click", (e) => {
  if (e.target === previewOverlay) closePreview();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && previewOverlay?.classList.contains("open")) closePreview();
});

// ---------------------------------------------------------------------------
// Grilla de plantillas.
// ---------------------------------------------------------------------------
const gridEl = document.getElementById("templatesGrid");

function renderEmptyState() {
  if (searchTerm) {
    gridEl.innerHTML =
      '<div class="empty-state-block"><p>No hay plantillas que coincidan con tu búsqueda.</p></div>';
    return;
  }
  gridEl.innerHTML = `
    <div class="empty-state-block">
      <p>Todavía no creaste ninguna plantilla propia.</p>
      <a class="btn btn-primary" href="${EDITOR_URL}">Diseñar mi primera plantilla</a>
    </div>
  `;
}

function buildCard(template) {
  const card = document.createElement("div");
  card.className = "template-card";

  const thumbBtn = document.createElement("button");
  thumbBtn.type = "button";
  thumbBtn.className = "template-thumb";
  if (template.preview) {
    const img = document.createElement("img");
    img.src = template.preview;
    img.alt = template.nombre || "Plantilla";
    thumbBtn.appendChild(img);
  } else {
    thumbBtn.classList.add("template-thumb-empty");
    thumbBtn.textContent = "Ver vista previa";
  }
  thumbBtn.addEventListener("click", () => openPreview(template));

  const body = document.createElement("div");
  body.className = "template-card-body";
  const badgeClass = template.isPublic ? "template-badge-public" : "template-badge-own";
  const badgeText = template.isPublic ? "Pública" : "Propia";
  body.innerHTML = `
    <div class="template-card-title-row">
      <h3 class="template-card-title">${template.nombre || "Plantilla sin nombre"}</h3>
      <span class="template-badge ${badgeClass}">${badgeText}</span>
    </div>
    <p class="template-card-meta">${template.size.widthCm} × ${template.size.heightCm} cm</p>
    <p class="template-card-meta">Modificada: ${formatDate(template.updatedAt)}</p>
  `;

  const actions = document.createElement("div");
  actions.className = "template-card-actions";

  const useBtn = document.createElement("a");
  useBtn.className = "btn btn-primary";
  useBtn.textContent = "Usar";
  useBtn.href = `${EDITOR_URL}?template=${template.id}`;
  actions.appendChild(useBtn);

  const duplicateBtn = document.createElement("button");
  duplicateBtn.type = "button";
  duplicateBtn.className = "btn btn-outline";
  duplicateBtn.textContent = "Duplicar";
  duplicateBtn.addEventListener("click", () => handleDuplicate(template));
  actions.appendChild(duplicateBtn);

  if (canManageTemplate(template)) {
    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "btn btn-outline";
    editBtn.textContent = "Editar";
    editBtn.addEventListener("click", () => openRenamePrompt(template));
    actions.appendChild(editBtn);

    const archiveBtn = document.createElement("button");
    archiveBtn.type = "button";
    archiveBtn.className = "btn btn-danger";
    archiveBtn.textContent = "Archivar";
    archiveBtn.addEventListener("click", () => handleArchive(template));
    actions.appendChild(archiveBtn);
  }

  card.append(thumbBtn, body, actions);
  return card;
}

function renderGrid(templates) {
  if (!templates.length) {
    renderEmptyState();
    return;
  }
  gridEl.innerHTML = "";
  templates.forEach((template) => gridEl.appendChild(buildCard(template)));
}

async function loadTemplates() {
  gridEl.innerHTML = '<p class="empty-state">Cargando plantillas...</p>';
  try {
    const templates = await templatesApi.listTemplates(searchTerm ? { search: searchTerm } : {});
    renderGrid(templates);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar las plantillas:", err);
    gridEl.innerHTML = '<p class="empty-state">No se pudieron cargar tus plantillas.</p>';
  }
}

// "Editar" acá es solo renombrar/redescribir desde la grilla: el diseño en
// sí (posiciones, tamaño) se edita abriendo la plantilla en el editor
// ("Usar" ya cubre eso, y guardar de nuevo como plantilla actualiza el
// diseño). Evita duplicar el editor completo para un caso de uso chico.
async function openRenamePrompt(template) {
  const name = window.prompt("Nuevo nombre de la plantilla:", template.nombre || "");
  if (!name || name.trim() === template.nombre) return;
  try {
    await templatesApi.updateTemplate(template.id, { nombre: name.trim() });
    showMessage(`Plantilla renombrada a "${name.trim()}".`, "success");
    await loadTemplates();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al editar la plantilla:", err);
    showMessage(err.message || "No se pudo editar la plantilla.");
  }
}

async function handleDuplicate(template) {
  try {
    await templatesApi.duplicateTemplate(template.id);
    showMessage(`Se duplicó "${template.nombre || "la plantilla"}".`, "success");
    await loadTemplates();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al duplicar la plantilla:", err);
    showMessage(err.message || "No se pudo duplicar la plantilla.");
  }
}

async function handleArchive(template) {
  const confirmed = window.confirm(`¿Archivar "${template.nombre || "esta plantilla"}"?`);
  if (!confirmed) return;
  try {
    await templatesApi.archiveTemplate(template.id);
    showMessage("Plantilla archivada.", "success");
    await loadTemplates();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al archivar la plantilla:", err);
    showMessage(err.message || "No se pudo archivar la plantilla.");
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
    loadTemplates();
  }, SEARCH_DEBOUNCE_MS);
});

// ---------------------------------------------------------------------------
// Logout: misma lógica que rotulos.js/documentos.js (window.Auth.logout).
// ---------------------------------------------------------------------------
const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) {
  logoutBtn.addEventListener("click", () => window.Auth.logout());
}

async function init() {
  // frontend/pedidos/api.js es la única fuente de verdad del CRUD de
  // plantillas (import dinámico: este script no es un módulo). Ruta
  // relativa a ESTE archivo (assets/js/plantillas.js), no a la página que
  // lo carga — mismo criterio que assets/js/rotulos.js.
  templatesApi = await import("../../pedidos/api.js");
  renderTopbar();
  await loadTemplates();
}

if (!getAccessToken()) {
  window.location.replace("index.html");
} else {
  init();
}
