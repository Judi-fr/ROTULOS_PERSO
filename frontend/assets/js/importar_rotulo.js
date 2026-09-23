// Importar rótulo desde una foto (importar_rotulo.html): la pantalla que
// faltaba de apps.processing.
//
// Son tres pasos y tres endpoints distintos, a propósito (ver el docstring de
// apps/processing/urls.py):
//
//   1. POST /documents/documentos/      sube la foto
//   2. POST /processing/label-imports/  el modelo la lee -> propuesta
//   3. POST /labels/element-layouts/    el usuario guarda lo que revisó
//
// El paso 2 NO guarda ninguna plantilla: devuelve una propuesta y nada más.
// Que el paso 3 sea una acción explícita del usuario es la garantía de que
// una lectura equivocada no le ensucia el catálogo de plantillas.
//
// Sesión y apiFetch salen de assets/js/auth.js (window.Auth); escapeHtml,
// showMessage y getErrorMessage, de assets/js/utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const ME_URL = `${API_BASE}/auth/me/`;
const UPLOAD_URL = `${API_BASE}/documents/documentos/`;
const IMPORTS_URL = `${API_BASE}/processing/label-imports/`;
const LAYOUTS_URL = `${API_BASE}/labels/element-layouts/`;

const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

const fileInput = document.getElementById("fileInput");
const readBtn = document.getElementById("readBtn");
const retryBtn = document.getElementById("retryBtn");
const reviewCard = document.getElementById("reviewCard");
const saveCard = document.getElementById("saveCard");
const readingSummary = document.getElementById("readingSummary");
const previewEl = document.getElementById("preview");
const elementListEl = document.getElementById("elementList");
const discardedBox = document.getElementById("discardedBox");
const saveForm = document.getElementById("saveForm");
const saveBtn = document.getElementById("saveBtn");
const startOverBtn = document.getElementById("startOverBtn");
const nameInput = document.getElementById("layoutName");
const descriptionInput = document.getElementById("layoutDescription");
const widthInput = document.getElementById("layoutWidth");
const heightInput = document.getElementById("layoutHeight");

// La propuesta que devolvió la lectura, tal cual. Se guarda entera porque el
// cuerpo que se manda en el paso 3 es esta misma propuesta sin "_revision".
let proposal = null;
// Id de la importación, para poder reintentar la MISMA foto sin volver a subirla.
let importId = null;

// ---------------------------------------------------------------------------
// Paso 1 y 2: subir y leer
// ---------------------------------------------------------------------------
fileInput.addEventListener("change", () => {
  readBtn.disabled = !fileInput.files.length;
});

async function uploadFile(file) {
  const body = new FormData();
  body.append("file", file);
  // Sin Content-Type a mano: el navegador tiene que poner el boundary del
  // multipart, y fijarlo acá lo rompe.
  const response = await apiFetch(UPLOAD_URL, { method: "POST", body });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(getErrorMessage(data, "No se pudo subir la foto."));
  }
  return data;
}

async function readLabel(uploadedFileId) {
  const response = await apiFetch(IMPORTS_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ uploaded_file: uploadedFileId }),
  });
  const data = await response.json().catch(() => ({}));
  // La vista contesta 502 cuando el modelo falló, con el motivo en "error":
  // no es un error de red, es una lectura que no salió.
  if (!response.ok) {
    throw new Error(data.error || getErrorMessage(data, "No se pudo leer el rótulo."));
  }
  return data;
}

readBtn.addEventListener("click", async () => {
  const file = fileInput.files[0];
  if (!file) return;
  readBtn.disabled = true;
  readBtn.textContent = "Leyendo...";
  try {
    const uploaded = await uploadFile(file);
    const labelImport = await readLabel(uploaded.id);
    importId = labelImport.id;
    showProposal(labelImport);
    showMessage("Listo. Revisá la propuesta antes de guardarla.", "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al importar el rótulo:", err);
    showMessage(err.message || "No se pudo importar el rótulo.");
  } finally {
    readBtn.disabled = false;
    readBtn.textContent = "Leer el rótulo";
  }
});

retryBtn.addEventListener("click", async () => {
  if (!importId) return;
  retryBtn.disabled = true;
  retryBtn.textContent = "Leyendo...";
  try {
    const response = await apiFetch(`${IMPORTS_URL}${importId}/retry/`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || "No se pudo volver a leer el rótulo.");
    }
    importId = data.id;
    showProposal(data);
    showMessage("Lectura nueva lista.", "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al reintentar la lectura:", err);
    showMessage(err.message || "No se pudo volver a leer el rótulo.");
  } finally {
    retryBtn.disabled = false;
    retryBtn.textContent = "Volver a leer";
  }
});

// ---------------------------------------------------------------------------
// Mostrar la propuesta
// ---------------------------------------------------------------------------
function confidenceLabel(value) {
  if (value === null || value === undefined) return "sin dato";
  const percent = Math.round(value * 100);
  if (percent >= 80) return `${percent}% — alta`;
  if (percent >= 50) return `${percent}% — media, revisá con cuidado`;
  return `${percent}% — baja, probablemente haya que corregir bastante`;
}

function elementLabel(element) {
  if (element.element_type === "variable") {
    return element.variable ? `Dato: ${element.variable}` : "Dato sin identificar";
  }
  if (element.element_type === "texto_estatico") {
    return `Texto fijo: ${element.content || "(vacío)"}`;
  }
  if (element.element_type === "linea") return "Línea";
  if (element.element_type === "recuadro") return "Recuadro";
  return element.element_type;
}

// Dibuja el rótulo a escala. Las posiciones vienen en milímetros sobre el
// tamaño total, así que alcanza con un factor: no hace falta pedirle al
// backend que lo renderice para que el usuario controle si está bien.
function renderPreview() {
  const widthMm = Number(widthInput.value) || proposal.width_mm;
  const heightMm = Number(heightInput.value) || proposal.height_mm;
  const maxWidthPx = Math.min(previewEl.clientWidth || 520, 520);
  const scale = maxWidthPx / widthMm;

  previewEl.innerHTML = "";
  const sheet = document.createElement("div");
  sheet.className = "preview-sheet";
  sheet.style.width = `${widthMm * scale}px`;
  sheet.style.height = `${heightMm * scale}px`;

  (proposal.elements || []).forEach((element, index) => {
    const box = document.createElement("div");
    box.className = `preview-element type-${escapeHtml(element.element_type)}`;
    box.style.left = `${element.x_mm * scale}px`;
    box.style.top = `${element.y_mm * scale}px`;
    box.style.width = `${element.width_mm * scale}px`;
    box.style.height = `${element.height_mm * scale}px`;
    box.title = elementLabel(element);
    box.textContent = index + 1;
    sheet.appendChild(box);
  });

  previewEl.appendChild(sheet);
}

function renderElements() {
  elementListEl.innerHTML = "";
  const detected = (proposal._revision && proposal._revision.detected_values) || [];
  const valueByVariable = new Map(detected.map((item) => [item.variable, item.value]));

  (proposal.elements || []).forEach((element, index) => {
    const row = document.createElement("div");
    row.className = "element-item";
    const detectedValue = valueByVariable.get(element.variable);
    row.innerHTML = `
      <span class="element-index">${index + 1}</span>
      <div class="element-info">
        <p class="element-title">${escapeHtml(elementLabel(element))}</p>
        <p class="element-meta">
          ${element.width_mm} × ${element.height_mm} mm · posición ${element.x_mm}, ${element.y_mm} mm
        </p>
        ${detectedValue ? `<p class="element-detected">Leyó: “${escapeHtml(String(detectedValue))}”</p>` : ""}
      </div>
    `;
    elementListEl.appendChild(row);
  });

  if (!(proposal.elements || []).length) {
    elementListEl.innerHTML =
      '<p class="empty-state">No reconoció ningún elemento. Probá con una foto más derecha o mejor iluminada.</p>';
  }
}

function renderSummary(labelImport) {
  const revision = proposal._revision || {};
  readingSummary.innerHTML = `
    <p class="summary-line"><strong>Confianza de la lectura:</strong> ${escapeHtml(confidenceLabel(revision.confidence))}</p>
    <p class="summary-line"><strong>Tamaño leído:</strong> ${proposal.width_mm} × ${proposal.height_mm} mm (${escapeHtml(proposal.orientation || "")})</p>
    <p class="summary-line"><strong>Archivo:</strong> ${escapeHtml(labelImport.uploaded_file_name || "")}</p>
    ${revision.notes ? `<p class="summary-note">${escapeHtml(revision.notes)}</p>` : ""}
  `;
}

function renderDiscarded() {
  const discarded = (proposal._revision && proposal._revision.discarded) || [];
  if (!discarded.length) {
    discardedBox.style.display = "none";
    return;
  }
  const reasons = discarded
    .map((item) => `<li>${escapeHtml(String(item.reason || "sin motivo"))}</li>`)
    .join("");
  discardedBox.innerHTML = `
    <p><strong>${discarded.length} cosa(s) que vio pero descartó:</strong></p>
    <ul>${reasons}</ul>
    <p class="field-hint">Si alguna te hace falta, vas a poder agregarla a mano en el editor después de guardar.</p>
  `;
  discardedBox.style.display = "block";
}

function showProposal(labelImport) {
  proposal = labelImport.proposal;
  if (!proposal) {
    showMessage("La lectura no devolvió ninguna propuesta.");
    return;
  }
  nameInput.value = proposal.name || "";
  descriptionInput.value = proposal.description || "";
  widthInput.value = proposal.width_mm;
  heightInput.value = proposal.height_mm;

  renderSummary(labelImport);
  renderElements();
  renderDiscarded();
  reviewCard.style.display = "";
  saveCard.style.display = "";
  renderPreview();
  reviewCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

// Cambiar las medidas reacomoda la vista: las posiciones son proporcionales.
widthInput.addEventListener("input", () => proposal && renderPreview());
heightInput.addEventListener("input", () => proposal && renderPreview());

// ---------------------------------------------------------------------------
// Paso 3: guardar
// ---------------------------------------------------------------------------
saveForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!proposal) return;

  // "_revision" es para que el usuario controle la lectura; el serializer de
  // plantillas no lo acepta, así que no viaja.
  const { _revision, ...body } = proposal;
  body.name = nameInput.value.trim();
  body.description = descriptionInput.value.trim();
  body.width_mm = Number(widthInput.value);
  body.height_mm = Number(heightInput.value);

  saveBtn.disabled = true;
  try {
    const response = await apiFetch(LAYOUTS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudo guardar la plantilla."));
    }
    showMessage("Plantilla guardada. Ya la podés usar para imprimir.", "success");
    resetAll();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al guardar la plantilla:", err);
    showMessage(err.message || "No se pudo guardar la plantilla.");
  } finally {
    saveBtn.disabled = false;
  }
});

function resetAll() {
  proposal = null;
  importId = null;
  fileInput.value = "";
  readBtn.disabled = true;
  reviewCard.style.display = "none";
  saveCard.style.display = "none";
  previewEl.innerHTML = "";
  elementListEl.innerHTML = "";
  discardedBox.style.display = "none";
}

startOverBtn.addEventListener("click", () => {
  resetAll();
  showMessage("Listo, elegí otra foto.", "success");
});

const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) {
  logoutBtn.addEventListener("click", () => window.Auth.logout());
}

async function init() {
  try {
    const response = await apiFetch(ME_URL);
    if (response.ok) {
      const user = await response.json();
      window.AppTopbar.render(user);
      // Gateo de UI nada más: el backend revalida processing.import y
      // plantillas.edit en cada llamada.
      const permissions = user.permissions || [];
      if (!permissions.includes("processing.import")) {
        window.location.replace("dashboard.html");
      }
    }
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar el perfil:", err);
  }
}

init();
