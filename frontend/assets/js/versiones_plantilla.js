// Versiones de una plantilla (versiones_plantilla.html?template=<id>): el
// historial de apps.labels.LabelTemplateVersion, la vista previa en PDF de
// cada versión, la comparación entre dos y restaurar una anterior. Qué se
// puede hacer lo decide el backend (`can_edit` de la plantilla, y de nuevo en
// cada request); acá solo se muestra. La API vive en labels_api.js (import
// dinámico: este script es clásico, igual que templates.js).

const hasSession = () => window.Auth.hasSession();
const templateId = Number(new URLSearchParams(window.location.search).get("template"));

let api = null; // assets/js/labels_api.js, cargado en init()
let template = null;
let versions = []; // de la más nueva a la más vieja
let compareA = null; // la más vieja de las dos que se comparan
let compareB = null;
let currentPreviewUrl = null;

const versionsBody = document.getElementById("versionsBody");
const compareBody = document.getElementById("compareBody");
const compareTitle = document.getElementById("compareTitle");

// Nombres que ve el usuario para las claves del diseño (las claves no se
// renombran: son datos guardados, ver CLAUDE.md).
const FIELD_LABELS = {
  remitente: "Remitente",
  destinatario: "Destinatario",
  domicilio: "Domicilio",
  cp: "Código postal",
  localidad: "Localidad",
  pedido: "N° de pedido",
  logo: "Logo",
  qr: "Código QR",
  barcode: "Código de barras",
  texts: "Textos fijos",
  lines: "Líneas",
  border: "Recuadro",
  rules: "Reglas según los datos del envío",
  locked: "Elementos bloqueados",
};

const PROP_LABELS = {
  left: "posición",
  top: "posición",
  text: "texto",
  font_size: "tamaño de letra",
  bold: "negrita",
  align: "alineación",
  width: "ancho",
  hide_if_empty: "ocultar si está vacío",
  shrink_to_fit: "achicar para que entre",
};

function formatDateTime(iso) {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("es-AR", { dateStyle: "short", timeStyle: "short" });
}

function sameValue(a, b) {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

// --- Comparación ------------------------------------------------------------

// Lista de cambios de `older` a `newer`: [{kind: added|removed|changed, what, detail}].
function diffVersions(older, newer) {
  const changes = [];
  if (older.nombre !== newer.nombre) {
    changes.push({ kind: "changed", what: "Nombre", detail: `«${older.nombre}» → «${newer.nombre}»` });
  }
  const oldSize = `${older.size.widthCm} × ${older.size.heightCm} cm`;
  const newSize = `${newer.size.widthCm} × ${newer.size.heightCm} cm`;
  if (oldSize !== newSize) {
    changes.push({ kind: "changed", what: "Tamaño", detail: `${oldSize} → ${newSize}` });
  }

  const keys = [...new Set([...Object.keys(older.fields), ...Object.keys(newer.fields)])];
  keys.forEach((key) => {
    const what = FIELD_LABELS[key] || key;
    const before = older.fields[key];
    const after = newer.fields[key];
    if (before === undefined) {
      changes.push({ kind: "added", what, detail: "" });
    } else if (after === undefined) {
      changes.push({ kind: "removed", what, detail: "" });
    } else if (!sameValue(before, after)) {
      changes.push({ kind: "changed", what, detail: describeChange(before, after) });
    }
  });
  return changes;
}

function describeChange(before, after) {
  const isPlainObject = (value) => value && typeof value === "object" && !Array.isArray(value);
  if (!isPlainObject(before) || !isPlainObject(after)) return "cambió";
  const props = [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter((prop) => !sameValue(before[prop], after[prop]))
    .map((prop) => PROP_LABELS[prop] || prop);
  const unique = [...new Set(props)];
  if (unique.includes("texto") && typeof before.text === "string" && typeof after.text === "string") {
    const index = unique.indexOf("texto");
    unique[index] = `texto: «${before.text}» → «${after.text}»`;
  }
  return unique.join(", ");
}

const KIND_LABELS = { added: "Agregado", removed: "Quitado", changed: "Cambió" };

function renderComparison() {
  const older = versions.find((v) => v.number === compareA);
  const newer = versions.find((v) => v.number === compareB);
  if (!older || !newer || older === newer) {
    compareTitle.textContent = "Qué cambió";
    compareBody.innerHTML = '<p class="empty-state">Elegí dos versiones distintas en la columna "Comparar".</p>';
    return;
  }
  compareTitle.textContent = `Qué cambió de la versión ${older.number} a la ${newer.number}`;
  const changes = diffVersions(older, newer);
  if (!changes.length) {
    compareBody.innerHTML = '<p class="empty-state">Las dos versiones dibujan lo mismo.</p>';
    return;
  }
  compareBody.innerHTML = `<ul class="diff-list">${changes
    .map(
      (change) => `<li>
        <span class="diff-kind diff-${change.kind}">${KIND_LABELS[change.kind]}</span>
        <strong>${escapeHtml(change.what)}</strong>
        ${change.detail ? `<span class="diff-detail"> — ${escapeHtml(change.detail)}</span>` : ""}
      </li>`
    )
    .join("")}</ul>`;
}

// --- Historial --------------------------------------------------------------

function renderVersions() {
  if (!versions.length) {
    versionsBody.innerHTML = '<tr><td colspan="6" class="empty-state">Esta plantilla todavía no tiene versiones.</td></tr>';
    return;
  }
  const current = versions[0].number;
  versionsBody.innerHTML = "";
  versions.forEach((version) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td class="version-number">${version.number}${version.number === current ? '<span class="version-current">Actual</span>' : ""}</td>
      <td>${escapeHtml(formatDateTime(version.createdAt))}</td>
      <td>${escapeHtml(version.createdByEmail || "-")}</td>
      <td>${escapeHtml(version.note || "-")}</td>
      <td class="version-compare">
        <label title="Versión de origen"><input type="radio" name="compareA" value="${version.number}" ${version.number === compareA ? "checked" : ""} /> A</label>
        <label title="Versión de destino"><input type="radio" name="compareB" value="${version.number}" ${version.number === compareB ? "checked" : ""} /> B</label>
      </td>
      <td><div class="version-actions"></div></td>
    `;
    const actions = row.querySelector(".version-actions");

    const previewBtn = document.createElement("button");
    previewBtn.type = "button";
    previewBtn.className = "btn btn-outline";
    previewBtn.textContent = "Ver cómo sale";
    previewBtn.addEventListener("click", () => openPreview(version));
    actions.appendChild(previewBtn);

    if (template.canEdit && version.number !== current) {
      const restoreBtn = document.createElement("button");
      restoreBtn.type = "button";
      restoreBtn.className = "btn btn-primary";
      restoreBtn.textContent = "Restaurar";
      restoreBtn.addEventListener("click", () => handleRestore(version, restoreBtn));
      actions.appendChild(restoreBtn);
    }
    versionsBody.appendChild(row);
  });

  versionsBody.querySelectorAll('input[name="compareA"]').forEach((input) =>
    input.addEventListener("change", () => {
      compareA = Number(input.value);
      renderComparison();
    })
  );
  versionsBody.querySelectorAll('input[name="compareB"]').forEach((input) =>
    input.addEventListener("change", () => {
      compareB = Number(input.value);
      renderComparison();
    })
  );
}

async function handleRestore(version, button) {
  const ok = await appConfirm(
    `¿Volver la plantilla a la versión ${version.number}? ` +
      "La actual no se pierde: queda en el historial y podés volver a ella."
  );
  if (!ok) return;
  button.disabled = true;
  try {
    template = await api.restoreTemplateVersion(templateId, version.number);
    await loadVersions();
    showMessage(
      `Listo: la plantilla volvió a la versión ${version.number} y quedó como versión ${template.currentVersion}.`,
      "success"
    );
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo restaurar esa versión.");
    button.disabled = false;
  }
}

async function loadVersions() {
  versions = await api.listTemplateVersions(templateId);
  // Por defecto: la anterior contra la actual.
  compareB = versions[0]?.number ?? null;
  compareA = versions[1]?.number ?? null;
  renderVersions();
  renderComparison();
}

// --- Vista previa (PDF del servidor de esa versión) -------------------------

const previewOverlay = document.getElementById("previewOverlay");
const previewFrame = document.getElementById("previewFrame");
const previewLoading = document.getElementById("previewLoading");

async function openPreview(version) {
  document.getElementById("previewNombre").textContent = `${version.nombre} — versión ${version.number}`;
  document.getElementById("previewMeta").textContent =
    `${version.size.widthCm} × ${version.size.heightCm} cm · con datos de ejemplo`;
  previewFrame.hidden = true;
  previewLoading.textContent = "Generando vista previa...";
  previewLoading.style.display = "block";
  previewOverlay.classList.add("open");
  try {
    const blob = await api.fetchTemplateVersionPreviewPdf(templateId, version.number);
    if (currentPreviewUrl) URL.revokeObjectURL(currentPreviewUrl);
    currentPreviewUrl = URL.createObjectURL(blob);
    previewFrame.src = currentPreviewUrl;
    previewFrame.hidden = false;
    previewLoading.style.display = "none";
  } catch (err) {
    if (err.isSessionExpired) return;
    previewLoading.textContent = err.message || "No se pudo generar la vista previa.";
  }
}

function closePreview() {
  previewOverlay.classList.remove("open");
  previewFrame.src = "";
  if (currentPreviewUrl) {
    URL.revokeObjectURL(currentPreviewUrl);
    currentPreviewUrl = null;
  }
}

document.getElementById("previewCloseBtn").addEventListener("click", closePreview);
previewOverlay.addEventListener("click", (event) => {
  if (event.target === previewOverlay) closePreview();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && previewOverlay.classList.contains("open")) closePreview();
});

// --- Arranque ----------------------------------------------------------------

async function init() {
  // Ruta relativa a ESTE archivo, igual que templates.js.
  api = await import("./labels_api.js");
  window.AppTopbar.render();
  if (!templateId) {
    versionsBody.innerHTML = '<tr><td colspan="6" class="empty-state">Falta elegir la plantilla: volvé a Mis plantillas.</td></tr>';
    return;
  }
  template = await api.getTemplate(templateId);
  if (!template) {
    versionsBody.innerHTML = '<tr><td colspan="6" class="empty-state">No se encontró esa plantilla.</td></tr>';
    return;
  }
  document.getElementById("templateTitle").textContent = `Versiones de «${template.nombre}»`;
  try {
    await loadVersions();
  } catch (err) {
    if (err.isSessionExpired) return;
    versionsBody.innerHTML = `<tr><td colspan="6" class="empty-state">${escapeHtml(err.message)}</td></tr>`;
  }
}

if (!hasSession()) {
  window.location.replace("index.html");
} else {
  init();
}
