// Imprimir rótulos desde la lista de pedidos (backend: apps.labels batch +
// apps.orders). El vendedor tilda los pedidos que quiere y sale un solo
// archivo con todos sus rótulos, sin tener que anotar números a mano.
//
// La plantilla y el logo salen de la tienda de cada pedido cuando no se
// elige una a mano (ver apps.labels.batch_views._resolve_template).
//
// Sesión y apiFetch salen de assets/js/auth.js (window.Auth); showMessage,
// getErrorMessage, escapeHtml, formatDate y extractResults de utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const ME_URL = `${API_BASE}/auth/me/`;
const ORDERS_URL = `${API_BASE}/orders/`;
const TEMPLATES_URL = `${API_BASE}/labels/templates/`;
const BATCH_URL = `${API_BASE}/labels/batch/`;

const STATUS_LABELS = {
  created: "Creado",
  preparing: "En preparación",
  dispatched: "Despachado",
  in_transit: "En tránsito",
  delivered: "Entregado",
  cancelled: "Cancelado",
};

const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

// Los pedidos elegidos sobreviven al cambio de página y de filtro: el
// vendedor puede juntar pedidos de varias tiendas en una sola impresión.
const selectedIds = new Set();
let currentPage = 1;
let hasNextPage = false;

function showPrintMsg(text, ok) {
  const el = document.getElementById("printMsg");
  el.textContent = text;
  el.style.color = ok ? "var(--success-500)" : "var(--danger-500)";
  el.style.display = "block";
}

function updateSelectionCount() {
  const count = selectedIds.size;
  document.getElementById("selectionCount").textContent =
    count === 1 ? "1 seleccionado" : `${count} seleccionados`;
}

// Todo lo que viene de una tienda es texto de terceros: se escapa siempre.
function renderOrders(orders) {
  const container = document.getElementById("orderList");
  if (!orders.length) {
    container.innerHTML = '<p class="empty-state">No hay pedidos con estos filtros.</p>';
    return;
  }

  container.innerHTML = "";
  orders.forEach((order) => {
    const row = document.createElement("label");
    row.className = "print-order";
    const number = order.external_number || order.id;
    const recipient = order.address?.recipient_name || "";
    const store = order.store_connection
      ? `<span class="store-tag">${escapeHtml(order.store_name || "Tienda")}</span>`
      : "";
    row.innerHTML = `
      <input type="checkbox" value="${escapeHtml(order.id)}" ${selectedIds.has(order.id) ? "checked" : ""} />
      <span class="print-order-body">
        <span class="print-order-title">Pedido #${escapeHtml(number)}${store}</span>
        <span class="print-order-meta">${escapeHtml(recipient)}${recipient ? " · " : ""}${escapeHtml(addressSummary(order.address))}</span>
        <span class="print-order-meta">${escapeHtml(formatDate(order.created_at))} · ${escapeHtml(order.status_label || STATUS_LABELS[order.status] || order.status)}${order.labels_printed_at ? ` · Rótulo impreso ${escapeHtml(formatDate(order.labels_printed_at))}` : ""}</span>
      </span>
    `;
    row.querySelector("input").addEventListener("change", (event) => {
      if (event.target.checked) selectedIds.add(order.id);
      else selectedIds.delete(order.id);
      updateSelectionCount();
    });
    container.appendChild(row);
  });
}

// La paginación se guía por next/previous que manda el backend, no por una
// cuenta de tamaños de página (el backend decide cuántos entran).
function renderPager(data) {
  hasNextPage = Boolean(data.next);
  const pager = document.getElementById("pager");
  const multiPage = hasNextPage || currentPage > 1;
  pager.style.display = multiPage ? "flex" : "none";
  document.getElementById("pageInfo").textContent = `Página ${currentPage}`;
  document.getElementById("prevPageBtn").disabled = currentPage <= 1;
  document.getElementById("nextPageBtn").disabled = !hasNextPage;
}

async function loadOrders() {
  const container = document.getElementById("orderList");
  const store = document.getElementById("storeFilter").value;
  const status = document.getElementById("statusFilter").value;
  const printed = document.getElementById("printedFilter").value;
  const dateFrom = document.getElementById("dateFrom").value;
  const dateTo = document.getElementById("dateTo").value;
  const params = new URLSearchParams({ page: String(currentPage) });
  if (store) params.set("store", store);
  if (status) params.set("status", status);
  if (printed) params.set("labels_printed", printed);
  // El rango acota la LISTA; lo que se manda al lote siguen siendo los
  // pedidos tildados (order_ids). Así el filtro por tienda sigue valiendo,
  // que es algo que el selector "filters" del backend no contempla.
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);

  try {
    const response = await apiFetch(`${ORDERS_URL}?${params.toString()}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudieron cargar los pedidos."));
    }
    renderOrders(extractResults(data));
    renderPager(data);
    document.getElementById("selectAll").checked = false;
  } catch (err) {
    if (err.isSessionExpired) return;
    container.innerHTML = `<p class="empty-state">${escapeHtml(err.message)}</p>`;
  }
}

async function loadStoreFilter() {
  await fillStoreFilter(document.getElementById("storeFilter"), document.getElementById("storeFilterField"));
}

async function loadTemplates() {
  const select = document.getElementById("templateSelect");
  try {
    const response = await apiFetch(TEMPLATES_URL);
    if (!response.ok) return;
    extractResults(await response.json()).forEach((template) => {
      const option = document.createElement("option");
      option.value = template.id;
      option.textContent = template.name;
      select.appendChild(option);
    });
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar plantillas:", err);
  }
}

// Despachar es una acción por pedido (POST /orders/<id>/ship/): se hace
// después de que el lote salió bien, y un pedido que no se puede despachar
// (ya entregado, cancelado) no corta el resto.
async function markDispatched(orderIds) {
  let done = 0;
  const failed = [];
  for (const orderId of orderIds) {
    try {
      const response = await apiFetch(`${ORDERS_URL}${orderId}/ship/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: "dispatched" }),
      });
      if (response.ok) done += 1;
      else failed.push(orderId);
    } catch (err) {
      if (err.isSessionExpired) return { done, failed };
      failed.push(orderId);
    }
  }
  return { done, failed };
}

// El selector de "Formato" mezcla dos cosas que el backend recibe por
// separado: el tipo de salida y, para una térmica, su densidad. Se traduce
// acá para no hacerle elegir tres cosas al comerciante — sabe qué impresora
// tiene, no qué es un dpmm. Lo normal es no mandar dpmm y que salga de la
// configuración de la tienda; las opciones de "forzar" son para el que
// todavía no la cargó o imprime en otra. Una Zebra ignora page_layout: no
// hay hoja que aprovechar, el rollo ya viene troquelado.
const OUTPUT_OPTIONS = {
  label: { output: "pdf", page_layout: "label" },
  a4: { output: "pdf", page_layout: "a4" },
  // Sin dpmm: el backend usa la impresora configurada en la tienda de esos
  // pedidos, y 203 dpi si no configuró ninguna (ver _resolve_dpmm).
  zpl: { output: "zpl" },
  "zpl-203": { output: "zpl", dpmm: 8 },
  "zpl-300": { output: "zpl", dpmm: 12 },
};

function outputOptions(value) {
  // Una impresora configurada: el formato y la densidad salen de ella
  // (LabelBatchView con printer_id) y el lote va directo a su cola.
  if (value.startsWith("printer:")) return { printer_id: Number(value.slice("printer:".length)) };
  return OUTPUT_OPTIONS[value] || OUTPUT_OPTIONS.label;
}

// Las impresoras para "Mandar directo" (apps.printing); la predeterminada
// queda elegida. Sin impresoras el grupo no aparece.
async function loadPrinters() {
  try {
    const response = await apiFetch(`${API_BASE}/printing/printers/`);
    if (!response.ok) return;
    const printers = extractResults(await response.json());
    const group = document.getElementById("printerOptions");
    group.innerHTML = printers
      .map((printer) => {
        const where = printer.location ? ` · ${printer.location}` : "";
        return `<option value="printer:${printer.id}">${escapeHtml(printer.name + where)}</option>`;
      })
      .join("");
    group.hidden = !printers.length;
    const preferred = printers.find((printer) => printer.is_default);
    if (preferred) document.getElementById("pageLayoutSelect").value = `printer:${preferred.id}`;
  } catch (err) {
    if (err.isSessionExpired) throw err;
  }
}

// Sin impresora predeterminada, el formato preferido de la cuenta
// (organizacion.html) queda elegido.
async function applyPreferredFormat() {
  const select = document.getElementById("pageLayoutSelect");
  if (select.value.startsWith("printer:")) return;
  const preferred = (await loadAccountSettings()).default_print_format;
  if (preferred && OUTPUT_OPTIONS[preferred]) select.value = preferred;
}

async function printLabels() {
  const orderIds = Array.from(selectedIds);
  if (!orderIds.length) {
    showPrintMsg("Elegí al menos un pedido.", false);
    return;
  }

  const payload = {
    order_ids: orderIds,
    skip_existing: document.getElementById("skipExisting").checked,
    ...outputOptions(document.getElementById("pageLayoutSelect").value),
  };
  const templateId = document.getElementById("templateSelect").value;
  if (templateId) payload.template_id = Number(templateId);

  const button = document.getElementById("printBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Generando...";
  showPrintMsg("Generando los rótulos...", true);

  try {
    const response = await apiFetch(BATCH_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudieron generar los rótulos."));
    }
    // Mandado a una impresora pero no encolado (el lote falló, o la
    // impresora no tiene agente): se avisa y no se despacha nada. Un lote
    // grande (queued) todavía no tiene trabajo: lo crea el worker al terminar.
    if (payload.printer_id && !data.print_job && !data.queued) {
      throw new Error(data.print_error || "No se pudo mandar a la impresora.");
    }

    let dispatched = 0;
    if (document.getElementById("markDispatched").checked) {
      button.textContent = "Despachando...";
      const { done, failed } = await markDispatched(orderIds);
      dispatched = done;
      if (failed.length) {
        showPrintMsg(
          `Rótulos generados. Se despacharon ${done} de ${orderIds.length} pedidos: ` +
            "los demás ya estaban entregados o cancelados.",
          false
        );
        selectedIds.clear();
        updateSelectionCount();
        await loadOrders();
        return;
      }
    }

    // El archivo se arma en "Mis documentos": ahí se descarga, y esa página
    // confirma lo que se hizo (ver documentos.js, showBatchResult). Mandado
    // a una impresora, se sigue en la cola (cola_impresion.js).
    const result = new URLSearchParams({ labels: String(orderIds.length) });
    if (dispatched) result.set("dispatched", String(dispatched));
    if (data.print_job) {
      result.set("sent", String(data.print_job.id));
      window.location.href = `cola_impresion.html?${result}`;
      return;
    }
    if (data.queued && payload.printer_id) {
      result.set("generating", "1");
      window.location.href = `cola_impresion.html?${result}`;
      return;
    }
    if (data.queued) result.set("generating", "1");
    window.location.href = `documentos.html?${result}`;
  } catch (err) {
    if (err.isSessionExpired) return;
    showPrintMsg(err.message || "No se pudieron generar los rótulos.", false);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

document.getElementById("storeFilter").addEventListener("change", () => {
  currentPage = 1;
  loadOrders();
});
["dateFrom", "dateTo"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    currentPage = 1;
    loadOrders();
  });
});

["statusFilter", "printedFilter"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    currentPage = 1;
    loadOrders();
  });
});

// "Para imprimir" del panel abre imprimir_rotulos.html?rotulo=sin_imprimir
// (y &tienda= desde el desglose por tienda): los pendientes sin rótulo de esa
// selección, ya tildados TODOS (no solo los de la primera
// página), listos para "Generar rótulos". Sigue las páginas del backend hasta
// el tope de un lote.
const SELECT_ALL_MAX = 1000;

async function selectAllPending() {
  const params = new URLSearchParams({ status: "created,preparing", labels_printed: "0" });
  const store = document.getElementById("storeFilter").value;
  if (store) params.set("store", store);
  let url = `${ORDERS_URL}?${params.toString()}`;
  while (url && selectedIds.size < SELECT_ALL_MAX) {
    const response = await apiFetch(url);
    if (!response.ok) return;
    const data = await response.json();
    extractResults(data).forEach((order) => selectedIds.add(order.id));
    url = data.next;
  }
  document.querySelectorAll("#orderList input[type=checkbox]").forEach((checkbox) => {
    checkbox.checked = selectedIds.has(Number(checkbox.value));
  });
  updateSelectionCount();
  if (selectedIds.size) {
    const count = selectedIds.size;
    showPrintMsg(
      `${count === 1 ? "Quedó tildado el pedido" : `Quedaron tildados los ${count} pedidos`} sin rótulo. Revisá la plantilla y tocá «Generar rótulos».`,
      true
    );
  }
}
document.getElementById("selectAll").addEventListener("change", (event) => {
  document.querySelectorAll("#orderList input[type=checkbox]").forEach((checkbox) => {
    checkbox.checked = event.target.checked;
    const id = Number(checkbox.value);
    if (event.target.checked) selectedIds.add(id);
    else selectedIds.delete(id);
  });
  updateSelectionCount();
});
document.getElementById("prevPageBtn").addEventListener("click", () => {
  if (currentPage > 1) {
    currentPage -= 1;
    loadOrders();
  }
});
document.getElementById("nextPageBtn").addEventListener("click", () => {
  if (hasNextPage) {
    currentPage += 1;
    loadOrders();
  }
});
document.getElementById("printBtn").addEventListener("click", printLabels);
document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

async function init() {
  try {
    const response = await apiFetch(ME_URL);
    if (response.ok) window.AppTopbar.render(await response.json());
  } catch (err) {
    if (err.isSessionExpired) return;
  }
  await loadStoreFilter();
  await loadTemplates();
  await loadPrinters();
  await applyPreferredFormat();
  const query = new URLSearchParams(location.search);
  const fromDashboard = query.get("rotulo") === "sin_imprimir";
  if (fromDashboard) {
    document.getElementById("statusFilter").value = "created,preparing";
    document.getElementById("printedFilter").value = "0";
    // Una tienda del desglose del panel (&tienda=<id>|manual), si está en la lista.
    const storeFilter = document.getElementById("storeFilter");
    const store = query.get("tienda");
    if (store && [...storeFilter.options].some((option) => option.value === store)) storeFilter.value = store;
  }
  await loadOrders();
  updateSelectionCount();
  if (fromDashboard) await selectAllPending();
}

if (!window.Auth.hasSession()) {
  window.location.replace("index.html");
} else {
  init();
}
