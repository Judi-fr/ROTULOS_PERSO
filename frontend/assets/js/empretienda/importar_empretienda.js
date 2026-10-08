// Importar la planilla de ventas de Empretienda (importar_empretienda.html).
// Backend: POST /api/v1/integrations/empretienda/import/preview/ y .../confirm/
// (apps/integrations/providers/empretienda/). Tres pasos: elegir tienda y
// archivo, revisar qué columna se usó para cada dato y cómo quedan los pedidos
// (corrigiendo columnas si hace falta), y confirmar. La vista previa no guarda
// nada: el archivo queda en esta página y se vuelve a mandar al confirmar.
//
// Todo lo que sale de la planilla es texto de terceros: se inserta con
// textContent, nunca como HTML. Sesión y apiFetch salen de auth.js;
// showMessage, getErrorMessage y extractResults, de utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const STORES_URL = `${API_BASE}/integrations/stores/`;
const PREVIEW_URL = `${API_BASE}/integrations/empretienda/import/preview/`;
const CONFIRM_URL = `${API_BASE}/integrations/empretienda/import/confirm/`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

const RESULT_LABELS = { new: "Nuevo", update: "Se actualiza", error: "Con errores" };
const STATUS_LABELS = {
  dispatched: "despachado",
  in_transit: "en tránsito",
  delivered: "entregado",
  cancelled: "cancelado",
};

let currentFile = null;
let currentFields = [];
let readyCount = 0;

function el(id) {
  return document.getElementById(id);
}

async function loadStores() {
  try {
    const response = await apiFetch(STORES_URL);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    const stores = extractResults(await response.json()).filter(
      (store) => store.platform === "empretienda" && store.status === "active"
    );
    stores.forEach((store) => {
      const option = document.createElement("option");
      option.value = store.id;
      option.textContent = store.name || store.external_store_id;
      el("storeSelect").appendChild(option);
    });
    el("noStoresHint").style.display = stores.length ? "none" : "";
    // Llegando desde tiendas.html (?store=<id>), o con una sola tienda, queda elegida.
    const requested = new URLSearchParams(window.location.search).get("store");
    if (requested && stores.some((store) => String(store.id) === requested)) {
      el("storeSelect").value = requested;
    } else if (stores.length === 1) {
      el("storeSelect").value = String(stores[0].id);
    }
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar las tiendas:", err);
    showMessage("No se pudieron cargar tus tiendas.");
  }
}

function currentMapping() {
  const mapping = {};
  currentFields.forEach(({ field }) => {
    const select = el(`map_${field}`);
    if (select && select.value) mapping[field] = select.value;
  });
  return mapping;
}

function renderMapping(headers, fields, mapping) {
  currentFields = fields;
  const row = el("mappingRow");
  row.innerHTML = "";
  fields.forEach(({ field, label, required }) => {
    const wrapper = document.createElement("div");
    wrapper.className = "field";
    const labelEl = document.createElement("label");
    labelEl.htmlFor = `map_${field}`;
    labelEl.textContent = required ? `${label} *` : label;
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

function renderOrders(orders) {
  const body = el("ordersBody");
  body.innerHTML = "";
  orders.forEach((order) => {
    const tr = document.createElement("tr");
    tr.className = order.result === "error" ? "row-error" : "row-ok";
    tr.appendChild(cell(order.number ? `#${order.number}` : "—"));
    tr.appendChild(cell(order.recipient || "—"));
    tr.appendChild(cell(order.address || "—"));
    tr.appendChild(cell([order.city, order.state, order.postal_code].filter(Boolean).join(", ") || "—"));
    tr.appendChild(cell(String(order.items)));
    let resultText = RESULT_LABELS[order.result] || order.result;
    if (order.status && STATUS_LABELS[order.status]) resultText += ` · ${STATUS_LABELS[order.status]}`;
    // Las filas con error ya se pintan por .row-error (acciones_masivas.css).
    const result = cell(resultText, order.result === "error" ? "state" : "state state-ok");
    if (order.errors && order.errors.length) {
      const detail = document.createElement("small");
      detail.textContent = order.errors.join(" ");
      result.appendChild(document.createElement("br"));
      result.appendChild(detail);
    }
    tr.appendChild(result);
    body.appendChild(tr);
  });
}

function renderCounts(orders) {
  const box = el("countsRow");
  box.innerHTML = "";
  const errors = orders.filter((order) => order.result === "error").length;
  readyCount = orders.length - errors;
  const ok = document.createElement("span");
  ok.className = "count-chip count-ok";
  ok.textContent = `${readyCount} de ${orders.length} pedido(s) listos para importar`;
  box.appendChild(ok);
  if (errors) {
    const bad = document.createElement("span");
    bad.className = "count-chip count-error";
    bad.textContent = `${errors} con errores (no se importan)`;
    box.appendChild(bad);
  }
  el("reviewCount").textContent = readyCount === 1 ? "1 para importar" : `${readyCount} para importar`;
  el("confirmBtn").disabled = !readyCount;
  el("confirmBtn").textContent = readyCount === 1 ? "Importar 1 pedido" : `Importar ${readyCount} pedidos`;
}

function buildForm(useMapping) {
  const form = new FormData();
  form.append("store", el("storeSelect").value);
  form.append("file", currentFile);
  if (useMapping) form.append("mapping", JSON.stringify(currentMapping()));
  return form;
}

async function preview(useMapping) {
  if (!el("storeSelect").value) {
    showMessage("Elegí la tienda Empretienda.");
    return;
  }
  const file = useMapping ? currentFile : el("fileInput").files[0];
  if (!file) {
    showMessage("Elegí la planilla que exportaste de Empretienda.");
    return;
  }
  currentFile = file;

  const button = useMapping ? el("remapBtn") : el("previewBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Leyendo...";
  el("importResult").innerHTML = "";

  try {
    // Sin Content-Type: el navegador arma el multipart con su boundary.
    const response = await apiFetch(PREVIEW_URL, { method: "POST", body: buildForm(useMapping) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo leer la planilla."));

    renderMapping(data.headers || [], data.fields || [], data.mapping || {});
    el("reviewCard").style.display = "";
    if (data.missing && data.missing.length) {
      el("ordersBody").innerHTML = "";
      el("countsRow").innerHTML = "";
      el("reviewCount").textContent = "";
      el("confirmCard").style.display = "none";
      showMessage(
        `No se reconoció la columna de: ${data.missing.join(", ")}. Elegila abajo y tocá «Volver a leer».`
      );
      return;
    }
    const orders = data.orders || [];
    renderOrders(orders);
    renderCounts(orders);
    el("confirmCard").style.display = readyCount ? "" : "none";
    showMessage(
      readyCount
        ? `Se leyeron ${data.rows} fila(s), ${orders.length} pedido(s). Revisá y confirmá abajo.`
        : "Ningún pedido se puede importar: revisá el detalle de cada uno.",
      readyCount ? "success" : "error"
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
  const button = el("confirmBtn");
  button.disabled = true;
  button.textContent = "Importando...";
  try {
    const response = await apiFetch(CONFIRM_URL, { method: "POST", body: buildForm(true) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron importar los pedidos."));

    const parts = [`${data.created} nuevo(s)`, `${data.updated} actualizado(s)`];
    if (data.unchanged) parts.push(`${data.unchanged} sin cambios`);
    const result = el("importResult");
    result.innerHTML = "";
    const summary = document.createElement("p");
    summary.className = "filters-hint";
    summary.textContent = `Resultado: ${parts.join(", ")}.`;
    result.appendChild(summary);
    if (data.failed && data.failed.length) {
      const list = document.createElement("ul");
      data.failed.forEach((item) => {
        const li = document.createElement("li");
        li.textContent = `#${item.number || "(sin número)"}: ${item.errors.join(" ")}`;
        list.appendChild(li);
      });
      result.appendChild(list);
    }
    const link = document.createElement("a");
    link.href = "imprimir_rotulos.html";
    link.textContent = "Ir a imprimir los rótulos";
    result.appendChild(link);

    showMessage(
      `Listo: ${parts.join(", ")}${data.failed && data.failed.length ? `; ${data.failed.length} con errores (abajo)` : ""}.`,
      "success"
    );
    // Lo importado no se confirma dos veces por error: queda solo el resultado.
    el("reviewCard").style.display = "none";
    currentFile = null;
    el("fileInput").value = "";
    button.textContent = "Importar pedidos";
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudieron importar los pedidos.");
    button.disabled = false;
    button.textContent = "Importar pedidos";
  }
}

el("previewBtn").addEventListener("click", () => preview(false));
el("remapBtn").addEventListener("click", () => preview(true));
el("confirmBtn").addEventListener("click", confirmImport);
const resetReview = () => {
  // Otra planilla u otra tienda: lo revisado ya no vale.
  el("reviewCard").style.display = "none";
  el("confirmCard").style.display = "none";
};
el("fileInput").addEventListener("change", resetReview);
el("storeSelect").addEventListener("change", resetReview);
document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

if (!window.Auth.getAccessToken()) {
  window.location.replace("index.html");
} else {
  loadStores();
  apiFetch(`${API_BASE}/auth/me/`)
    .then(async (response) => {
      if (response.ok) window.AppTopbar.render(await response.json());
    })
    .catch(() => {});
}
