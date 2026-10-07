// Carga masiva de seguimientos desde la planilla del transportista (backend:
// POST /api/v1/orders/tracking-import/preview/ y .../confirm/, ver
// apps.orders.bulk_views). Tres pasos: subir la planilla, revisar cómo se
// cruzó cada fila con un pedido (y corregir las columnas si hace falta) y
// confirmar. El preview no guarda nada: el archivo queda en esta página y
// se vuelve a mandar si cambian las columnas.
//
// Todo lo que sale de la planilla o de una tienda es texto de terceros: se
// inserta con textContent, nunca como HTML.
//
// Sesión y apiFetch salen de auth.js; showMessage, getErrorMessage,
// extractResults y renderBulkResult, de utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const PREVIEW_URL = `${API_BASE}/orders/tracking-import/preview/`;
const CONFIRM_URL = `${API_BASE}/orders/tracking-import/confirm/`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

const MAPPING_FIELDS = [
  ["order", "Número de pedido"],
  ["tracking_number", "Número de seguimiento"],
  ["carrier", "Transportista"],
  ["tracking_url", "URL de seguimiento"],
];

const STATE_LABELS = {
  ok: "Listo para cargar",
  not_found: "Pedido no encontrado",
  ambiguous: "Número repetido",
  not_shippable: "No se puede actualizar",
  missing_tracking: "Sin seguimiento",
  missing_order: "Sin número de pedido",
  invalid_url: "URL inválida",
  invalid: "Dato demasiado largo",
  duplicate: "Repetido",
};

let currentFile = null;
let previewRows = [];

function el(id) {
  return document.getElementById(id);
}

async function loadStoreFilter() {
  try {
    const response = await apiFetch(`${API_BASE}/integrations/stores/`);
    if (!response.ok) return;
    const stores = extractResults(await response.json());
    if (stores.length < 2) return; // con una sola tienda no hay qué desempatar
    stores.forEach((store) => {
      const option = document.createElement("option");
      option.value = store.id;
      option.textContent = store.name || `Tienda ${store.external_store_id}`;
      el("storeFilter").appendChild(option);
    });
    const manual = document.createElement("option");
    manual.value = "manual";
    manual.textContent = "Pedidos cargados a mano";
    el("storeFilter").appendChild(manual);
    el("storeFilterField").style.display = "";
  } catch (err) {
    if (!err.isSessionExpired) console.error("Error al cargar tiendas:", err);
  }
}

function currentMapping() {
  const mapping = {};
  MAPPING_FIELDS.forEach(([field]) => {
    const select = el(`map_${field}`);
    if (select) mapping[field] = select.value || null;
  });
  return mapping;
}

function renderMapping(headers, mapping) {
  const row = el("mappingRow");
  row.innerHTML = "";
  MAPPING_FIELDS.forEach(([field, label]) => {
    const wrapper = document.createElement("div");
    wrapper.className = "field";
    const labelEl = document.createElement("label");
    labelEl.htmlFor = `map_${field}`;
    labelEl.textContent = `Columna: ${label}`;
    const select = document.createElement("select");
    select.id = `map_${field}`;
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "— ninguna —";
    select.appendChild(none);
    headers.forEach((header) => {
      const option = document.createElement("option");
      option.value = header;
      option.textContent = header;
      select.appendChild(option);
    });
    select.value = mapping[field] || "";
    wrapper.append(labelEl, select);
    row.appendChild(wrapper);
  });
}

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text ?? "";
  if (className) td.className = className;
  return td;
}

function renderRows(rows) {
  const body = el("rowsBody");
  body.innerHTML = "";
  rows.forEach((row, index) => {
    const tr = document.createElement("tr");
    tr.className = row.state === "ok" ? "row-ok" : "row-error";

    const checkTd = document.createElement("td");
    if (row.state === "ok") {
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = true;
      checkbox.dataset.index = String(index);
      checkbox.addEventListener("change", updateConfirmCount);
      checkTd.appendChild(checkbox);
    }
    tr.appendChild(checkTd);
    tr.appendChild(cell(row.row));
    tr.appendChild(cell(row.order_key ? `#${row.order_key}` : "—"));

    const order = row.order;
    let match = "—";
    if (order) {
      match = `#${order.number}`;
      if (order.recipient) match += ` · ${order.recipient}`;
      if (order.store_name) match += ` · ${order.store_name}`;
      match += ` (${order.status_label})`;
    }
    tr.appendChild(cell(match));

    let tracking = row.tracking_number || "—";
    if (order && order.tracking_number && order.tracking_number !== row.tracking_number) {
      tracking += ` (reemplaza ${order.tracking_number})`;
    }
    tr.appendChild(cell(tracking));
    tr.appendChild(cell(row.carrier || "—"));

    const result = cell(STATE_LABELS[row.state] || row.state, `state state-${row.state}`);
    if (row.message) result.title = row.message;
    if (row.message) {
      const detail = document.createElement("small");
      detail.textContent = row.message;
      result.appendChild(document.createElement("br"));
      result.appendChild(detail);
    }
    tr.appendChild(result);
    body.appendChild(tr);
  });
  el("selectAllRows").checked = true;
}

function renderCounts(data) {
  const box = el("countsRow");
  box.innerHTML = "";
  const counts = data.counts || {};
  const ok = counts.ok || 0;
  const total = data.rows.length;
  const summary = document.createElement("span");
  summary.className = "count-chip count-ok";
  summary.textContent = `${ok} de ${total} fila(s) listas para cargar`;
  box.appendChild(summary);
  Object.entries(counts).forEach(([state, count]) => {
    if (state === "ok") return;
    const chip = document.createElement("span");
    chip.className = "count-chip count-error";
    chip.textContent = `${count} · ${STATE_LABELS[state] || state}`;
    box.appendChild(chip);
  });
}

function selectedRows() {
  return Array.from(el("rowsBody").querySelectorAll("input[type=checkbox]:checked")).map(
    (checkbox) => previewRows[Number(checkbox.dataset.index)]
  );
}

function updateConfirmCount() {
  const count = selectedRows().length;
  el("reviewCount").textContent = count === 1 ? "1 para cargar" : `${count} para cargar`;
  el("confirmBtn").disabled = !count;
  el("confirmBtn").textContent =
    count === 1 ? "Cargar 1 seguimiento" : `Cargar ${count} seguimientos`;
}

async function preview(useMapping) {
  const file = useMapping ? currentFile : el("fileInput").files[0];
  if (!file) {
    showMessage("Elegí la planilla del transportista.");
    return;
  }
  currentFile = file;

  const form = new FormData();
  form.append("file", file);
  const carrier = el("carrierInput").value.trim();
  if (carrier) form.append("carrier", carrier);
  const store = el("storeFilter").value;
  if (store) form.append("store", store);
  if (useMapping) form.append("mapping", JSON.stringify(currentMapping()));

  const button = useMapping ? el("remapBtn") : el("previewBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Leyendo...";
  el("bulkResult").innerHTML = "";

  try {
    // Sin Content-Type: el navegador arma el multipart con su boundary.
    const response = await apiFetch(PREVIEW_URL, { method: "POST", body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo leer la planilla."));

    previewRows = data.rows || [];
    renderMapping(data.headers || [], data.mapping || {});
    el("reviewCard").style.display = "";
    if (!data.mapping.order || !data.mapping.tracking_number) {
      el("rowsBody").innerHTML = "";
      el("countsRow").innerHTML = "";
      el("confirmCard").style.display = "none";
      el("reviewCount").textContent = "";
      showMessage(
        "No se reconoció la columna del número de pedido o la del seguimiento. " +
          "Elegilas abajo y tocá «Volver a cruzar»."
      );
      return;
    }
    renderRows(previewRows);
    renderCounts(data);
    el("confirmCard").style.display = "";
    updateConfirmCount();
    const ok = data.counts?.ok || 0;
    showMessage(
      ok
        ? `Se leyeron ${data.total_rows} fila(s). Revisá y confirmá abajo.`
        : "Ninguna fila se puede cargar: revisá el detalle de cada una.",
      ok ? "success" : "error"
    );
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo leer la planilla.");
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

async function confirmImport() {
  const rows = selectedRows();
  if (!rows.length) {
    showMessage("No hay filas tildadas para cargar.");
    return;
  }
  const payload = {
    status: el("targetStatus").value,
    rows: rows.map((row) => {
      const item = { order_id: row.order.id, tracking_number: row.tracking_number };
      if (row.carrier) item.carrier = row.carrier;
      if (row.tracking_url) item.tracking_url = row.tracking_url;
      return item;
    }),
  };

  const button = el("confirmBtn");
  button.disabled = true;
  button.textContent = "Cargando...";
  try {
    const response = await apiFetch(CONFIRM_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar los seguimientos."));

    renderBulkResult(el("bulkResult"), data);
    showMessage(
      data.failed
        ? `Se cargaron ${data.updated} de ${rows.length} seguimiento(s). Abajo, por qué no los demás.`
        : `Listo: se cargaron ${data.updated} seguimiento(s).`,
      data.updated ? "success" : "error"
    );
    // Lo cargado ya no se puede volver a confirmar por error: se limpia la
    // revisión y queda solo el resultado.
    el("reviewCard").style.display = "none";
    el("confirmBtn").disabled = true;
    previewRows = [];
    currentFile = null;
    el("fileInput").value = "";
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudieron cargar los seguimientos.");
    button.disabled = false;
  } finally {
    if (previewRows.length) updateConfirmCount();
    else button.textContent = "Cargar seguimientos";
  }
}

el("previewBtn").addEventListener("click", () => preview(false));
el("remapBtn").addEventListener("click", () => preview(true));
el("confirmBtn").addEventListener("click", confirmImport);
el("selectAllRows").addEventListener("change", (event) => {
  el("rowsBody").querySelectorAll("input[type=checkbox]").forEach((checkbox) => {
    checkbox.checked = event.target.checked;
  });
  updateConfirmCount();
});
el("fileInput").addEventListener("change", () => {
  // Otra planilla: lo revisado de la anterior ya no vale.
  el("reviewCard").style.display = "none";
  el("confirmCard").style.display = "none";
  previewRows = [];
});
document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

if (!window.Auth.getAccessToken()) {
  window.location.replace("index.html");
} else {
  loadStoreFilter();
  apiFetch(`${API_BASE}/auth/me/`)
    .then(async (response) => {
      if (response.ok) window.AppTopbar.render(await response.json());
    })
    .catch(() => {});
}
