// Carga e importación de pedidos (importar.html): alta manual de un envío
// (story 20) e importación CSV/Excel en tres pasos -- subir, mapear,
// confirmar (stories 21-22), con plantillas de mapeo guardadas y la
// plantilla descargable. Sesión y apiFetch salen de assets/js/auth.js
// (window.Auth), igual que el resto del frontend.

const API_BASE = window.APP_CONFIG.API_BASE;
const ME_URL = `${API_BASE}/auth/me/`;
const MANUAL_ORDER_URL = `${API_BASE}/orders/manual/`;
const IMPORT_UPLOAD_URL = `${API_BASE}/orders/imports/`;
const IMPORT_TEMPLATE_URL = `${API_BASE}/orders/imports/template/`;
const MAPPINGS_URL = `${API_BASE}/orders/import-mappings/`;
const importValidateUrl = (id) => `${API_BASE}/orders/imports/${id}/validate/`;
const importConfirmUrl = (id) => `${API_BASE}/orders/imports/${id}/confirm/`;

const apiFetch = (url, options) => window.Auth.apiFetch(url, options);
const getAccessToken = () => window.Auth.getAccessToken();
const getCurrentUser = () => window.Auth.getCurrentUser();

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

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
  if (Array.isArray(data.detail)) return data.detail.join(" ");
  for (const value of Object.values(data)) {
    if (Array.isArray(value) && value.length) return String(value[0]);
    if (typeof value === "string") return value;
  }
  return fallback;
}

function extractResults(data) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.results)) return data.results;
  return [];
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

const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) logoutBtn.addEventListener("click", () => window.Auth.logout());

// ---------------------------------------------------------------------------
// Permisos (de /auth/me/): gatean la pestaña "a nombre de otro usuario" y,
// si faltan los dos permisos de carga operativa, esta pantalla entera --
// el link del dashboard ya no se muestra sin permiso, pero una URL directa
// tiene que redirigir igual (mismo criterio que gestionuser.html).
// ---------------------------------------------------------------------------
let currentPermissions = [];
function hasPermission(key) {
  return currentPermissions.includes(key);
}

// ---------------------------------------------------------------------------
// TABS
// ---------------------------------------------------------------------------
function activateTab(tab) {
  const isManual = tab === "manual";
  document.getElementById("tabManualBtn").classList.toggle("active", isManual);
  document.getElementById("tabImportBtn").classList.toggle("active", !isManual);
  document.getElementById("tabManualPanel").classList.toggle("active", isManual);
  document.getElementById("tabImportPanel").classList.toggle("active", !isManual);
}
document.getElementById("tabManualBtn")?.addEventListener("click", () => activateTab("manual"));
document.getElementById("tabImportBtn")?.addEventListener("click", () => activateTab("import"));

// ---------------------------------------------------------------------------
// ALTA MANUAL (story 20)
// ---------------------------------------------------------------------------
function showManualMsg(text, ok) {
  const el = document.getElementById("manualFormMsg");
  if (!el) return;
  el.textContent = text;
  el.className = `page-message ${ok ? "success" : "error"}`;
  el.style.display = "block";
}

document.getElementById("manualOrderForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = document.getElementById("manualSubmitBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Guardando...";

  const payload = {
    destinatario: document.getElementById("manualDestinatario").value.trim(),
    domicilio: document.getElementById("manualDomicilio").value.trim(),
    numero: document.getElementById("manualNumero").value.trim(),
    ciudad: document.getElementById("manualCiudad").value.trim(),
    provincia: document.getElementById("manualProvincia").value.trim(),
    cp: document.getElementById("manualCp").value.trim(),
    referencia: document.getElementById("manualReferencia").value.trim(),
    descripcion: document.getElementById("manualDescripcion").value.trim(),
    external_id: document.getElementById("manualExternalId").value.trim(),
  };
  const userId = document.getElementById("manualUserId").value.trim();
  if (userId) payload.user_id = userId;

  try {
    const response = await apiFetch(MANUAL_ORDER_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo dar de alta el envío."));

    const already = response.status === 200;
    showManualMsg(
      already
        ? `Ya existía un pedido con ese ID externo (#${data.id}); no se duplicó.`
        : `Envío #${data.id} dado de alta correctamente.`,
      true
    );
    event.target.reset();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al dar de alta el envío:", err);
    showManualMsg(err.message || "No se pudo dar de alta el envío.", false);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
});

// ---------------------------------------------------------------------------
// IMPORTACIÓN CSV/EXCEL (stories 21-22)
// ---------------------------------------------------------------------------
let currentImportId = null;
let currentHeaders = [];
let currentTargetFields = {};
let currentMapping = {};
let savedMappingTemplates = [];

function setImportStep(step) {
  document.querySelectorAll(".import-step").forEach((el) => {
    const n = Number(el.dataset.step);
    el.classList.toggle("active", n === step);
    el.classList.toggle("done", n < step);
  });
  document.getElementById("importUploadStep").style.display = step === 1 ? "" : "none";
  document.getElementById("importMappingStep").style.display = step === 2 ? "" : "none";
  document.getElementById("importValidationStep").style.display = step === 2.5 ? "" : "none";
  document.getElementById("importDoneStep").style.display = step === 3 ? "" : "none";
}

function showStepMsg(elId, text, ok) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.textContent = text;
  el.className = `page-message ${ok ? "success" : "error"}`;
  el.style.display = "block";
}

document.getElementById("downloadTemplateBtn")?.addEventListener("click", async () => {
  try {
    const response = await apiFetch(IMPORT_TEMPLATE_URL);
    if (!response.ok) throw new Error("No se pudo descargar la plantilla.");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "plantilla-importacion-pedidos.csv";
    link.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo descargar la plantilla.");
  }
});

function headerToTargetMap(mapping) {
  const result = {};
  Object.entries(mapping || {}).forEach(([target, header]) => {
    if (header) result[header] = target;
  });
  return result;
}

function renderMappingTable() {
  const body = document.getElementById("mappingTableBody");
  if (!body) return;
  const headerToTarget = headerToTargetMap(currentMapping);
  body.innerHTML = "";
  currentHeaders.forEach((header) => {
    const tr = document.createElement("tr");
    tr.dataset.header = header;
    const select = document.createElement("select");
    const noneOpt = document.createElement("option");
    noneOpt.value = "";
    noneOpt.textContent = "-- No mapear --";
    select.appendChild(noneOpt);
    Object.entries(currentTargetFields).forEach(([key, meta]) => {
      const opt = document.createElement("option");
      opt.value = key;
      opt.textContent = meta.required ? `${meta.label} *` : meta.label;
      if (headerToTarget[header] === key) opt.selected = true;
      select.appendChild(opt);
    });
    const tdHeader = document.createElement("td");
    tdHeader.textContent = header;
    const tdSelect = document.createElement("td");
    tdSelect.appendChild(select);
    tr.append(tdHeader, tdSelect);
    body.appendChild(tr);
  });
}

function readMappingFromTable() {
  const mapping = {};
  document.querySelectorAll("#mappingTableBody tr").forEach((tr) => {
    const header = tr.dataset.header;
    const select = tr.querySelector("select");
    if (select && select.value) mapping[select.value] = header;
  });
  return mapping;
}

function renderPreviewTable(headers, rows) {
  const head = document.getElementById("previewTableHead");
  const body = document.getElementById("previewTableBody");
  if (!head || !body) return;
  head.innerHTML = headers.map((h) => `<th>${escapeHtml(h)}</th>`).join("");
  body.innerHTML = rows
    .map(
      (row) =>
        `<tr>${headers.map((h) => `<td>${escapeHtml(row[h] ?? "")}</td>`).join("")}</tr>`
    )
    .join("");
}

async function loadMappingTemplates() {
  const select = document.getElementById("mappingTemplateSelect");
  if (!select) return;
  try {
    const response = await apiFetch(MAPPINGS_URL);
    if (!response.ok) return;
    const data = await response.json();
    savedMappingTemplates = extractResults(data);
    select.innerHTML = '<option value="">Aplicar una plantilla de mapeo guardada...</option>';
    savedMappingTemplates.forEach((tpl) => {
      const opt = document.createElement("option");
      opt.value = tpl.id;
      opt.textContent = tpl.name;
      select.appendChild(opt);
    });
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar plantillas de mapeo:", err);
  }
}

document.getElementById("mappingTemplateSelect")?.addEventListener("change", (event) => {
  const id = event.target.value;
  if (!id) return;
  const tpl = savedMappingTemplates.find((t) => String(t.id) === String(id));
  if (!tpl) return;
  currentMapping = tpl.mapping || {};
  renderMappingTable();
});

document.getElementById("saveMappingTemplateBtn")?.addEventListener("click", async () => {
  const nameInput = document.getElementById("mappingTemplateName");
  const name = nameInput.value.trim();
  if (!name) {
    showStepMsg("mappingMsg", "Poné un nombre para guardar la plantilla de mapeo.", false);
    return;
  }
  const mapping = readMappingFromTable();
  try {
    const response = await apiFetch(MAPPINGS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, mapping }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo guardar la plantilla."));
    showStepMsg("mappingMsg", `Plantilla "${data.name}" guardada.`, true);
    nameInput.value = "";
    await loadMappingTemplates();
  } catch (err) {
    if (err.isSessionExpired) return;
    showStepMsg("mappingMsg", err.message || "No se pudo guardar la plantilla.", false);
  }
});

document.getElementById("uploadFileBtn")?.addEventListener("click", async () => {
  const fileInput = document.getElementById("importFileInput");
  const file = fileInput.files && fileInput.files[0];
  if (!file) {
    showStepMsg("uploadMsg", "Elegí un archivo primero.", false);
    return;
  }
  const button = document.getElementById("uploadFileBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Subiendo...";

  const formData = new FormData();
  formData.append("file", file);

  try {
    const response = await apiFetch(IMPORT_UPLOAD_URL, { method: "POST", body: formData });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo subir el archivo."));

    currentImportId = data.id;
    currentHeaders = data.headers || [];
    currentTargetFields = data.target_fields || {};
    currentMapping = data.suggested_mapping || {};

    document.getElementById("importFileSummary").textContent = `${data.total_rows} fila(s) detectada(s)`;
    renderMappingTable();
    renderPreviewTable(currentHeaders, data.preview_rows || []);
    await loadMappingTemplates();
    setImportStep(2);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al subir el archivo:", err);
    showStepMsg("uploadMsg", err.message || "No se pudo subir el archivo.", false);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
});

document.getElementById("backToUploadBtn")?.addEventListener("click", () => {
  setImportStep(1);
});

function renderValidationErrors(elId, errors) {
  const el = document.getElementById(elId);
  if (!el) return;
  if (!errors || !errors.length) {
    el.innerHTML = '<p class="empty-state">Sin errores.</p>';
    return;
  }
  el.innerHTML = errors
    .map(
      (e) =>
        `<div class="error-list-item"><span class="row-number">Fila ${e.fila}:</span>${escapeHtml(e.mensaje)}</div>`
    )
    .join("");
}

document.getElementById("validateMappingBtn")?.addEventListener("click", async () => {
  currentMapping = readMappingFromTable();
  const button = document.getElementById("validateMappingBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Validando...";
  try {
    const response = await apiFetch(importValidateUrl(currentImportId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mapping: currentMapping }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo validar el mapeo."));

    document.getElementById("validCountValue").textContent = data.valid_count;
    document.getElementById("invalidCountValue").textContent = data.error_count;
    document.getElementById("totalRowsValue").textContent = data.total_rows;
    renderValidationErrors("validationErrorList", data.errors);
    setImportStep(2.5);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al validar el mapeo:", err);
    showStepMsg("mappingMsg", err.message || "No se pudo validar el mapeo.", false);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
});

document.getElementById("backToMappingBtn")?.addEventListener("click", () => setImportStep(2));

document.getElementById("confirmImportBtn")?.addEventListener("click", async () => {
  const button = document.getElementById("confirmImportBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Confirmando...";
  try {
    const response = await apiFetch(importConfirmUrl(currentImportId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mapping: currentMapping }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo confirmar la importación."));

    document.getElementById("importedCountValue").textContent = data.imported_count;
    document.getElementById("skippedCountValue").textContent = data.skipped_count;
    document.getElementById("errorCountValue").textContent = data.error_count;
    renderValidationErrors("doneErrorList", data.errors);
    setImportStep(3);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al confirmar la importación:", err);
    showMessage(err.message || "No se pudo confirmar la importación.");
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
});

document.getElementById("newImportBtn")?.addEventListener("click", () => {
  currentImportId = null;
  currentHeaders = [];
  currentTargetFields = {};
  currentMapping = {};
  document.getElementById("importFileInput").value = "";
  document.getElementById("uploadMsg").style.display = "none";
  setImportStep(1);
});

// ---------------------------------------------------------------------------
// INICIO
// ---------------------------------------------------------------------------
async function init() {
  try {
    const response = await apiFetch(ME_URL);
    if (response.ok) {
      const user = await response.json();
      renderTopbar(user);
      currentPermissions = Array.isArray(user.permissions) ? user.permissions : [];
    }
  } catch (err) {
    if (err.isSessionExpired) return;
  }

  const canManual = hasPermission("orders.create_manual");
  const canImport = hasPermission("orders.import");
  if (!canManual && !canImport) {
    window.location.replace("dashboard.html");
    return;
  }
  document.getElementById("manualUserIdField").style.display = hasPermission("orders.create_for_others")
    ? ""
    : "none";

  if (!canManual) {
    document.getElementById("tabManualBtn").style.display = "none";
    activateTab("import");
  }
  if (!canImport) {
    document.getElementById("tabImportBtn").style.display = "none";
  }
}

if (!getAccessToken()) {
  window.location.replace("index.html");
} else {
  init();
}
