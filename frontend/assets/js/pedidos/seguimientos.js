// "Cargar seguimientos" en Mis pedidos (pedidos.html, <dialog id="trackingDialog">):
// la planilla que manda el transportista (número de pedido + número de
// seguimiento) carga todos los seguimientos de una vez. Backend:
// POST /orders/tracking-import/preview/ y .../confirm/ (apps.orders.bulk_views).
//
// Es para los transportistas sin API: con Andreani el seguimiento ya se carga
// solo al despachar. Elegir el archivo ya lo lee; las columnas se reconocen
// solas y solo se piden si no; la tienda y el transportista se preguntan solo
// cuando la planilla los deja en duda. El preview no guarda nada.
//
// Todo lo que sale de la planilla o de una tienda es texto de terceros: se
// inserta con textContent.

window.OrderTrackingImport = (() => {
  const API_BASE = window.APP_CONFIG.API_BASE;
  const PREVIEW_URL = `${API_BASE}/orders/tracking-import/preview/`;
  const CONFIRM_URL = `${API_BASE}/orders/tracking-import/confirm/`;
  const apiFetch = (url, options) => window.Auth.apiFetch(url, options);
  const dialog = () => document.getElementById("trackingDialog");

  const MAPPING_FIELDS = [
    ["order", "Número de pedido"],
    ["tracking_number", "Número de seguimiento"],
    ["carrier", "Transportista"],
    ["tracking_url", "Link de seguimiento"],
  ];
  const STATE_LABELS = {
    ok: "Listo",
    not_found: "Pedido no encontrado",
    ambiguous: "Número repetido en dos tiendas",
    not_shippable: "No se puede actualizar",
    missing_tracking: "Sin seguimiento",
    missing_order: "Sin número de pedido",
    invalid_url: "Link inválido",
    invalid: "Dato demasiado largo",
    duplicate: "Repetido",
  };

  let file = null;
  let preview = null;
  let stores = null;
  let changed = false;

  function node(tag, { className = "", text = "", attrs = {} } = {}) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
    return element;
  }

  function afterClose() {
    if (!changed) return;
    changed = false;
    window.OrderShipping.reload();
  }

  function close() {
    dialog().close();
    afterClose();
  }

  function frame(title) {
    const box = dialog();
    box.replaceChildren();
    const header = node("div", { className: "dispatch-header" });
    const closeButton = node("button", { className: "dispatch-close", text: "×", attrs: { type: "button", "aria-label": "Cerrar" } });
    closeButton.onclick = close;
    header.append(node("h2", { text: title, attrs: { id: "trackingTitle" } }), closeButton);
    const body = node("div", { className: "dispatch-body" });
    const footer = node("div", { className: "dispatch-footer" });
    box.append(header, body, footer);
    return { body, footer };
  }

  function errorLine(body, text) {
    body.appendChild(node("p", { className: "dispatch-error", text }));
  }

  // ---------------------------------------------------------------------------
  // Paso 1: el archivo
  // ---------------------------------------------------------------------------
  function renderPick(message = "") {
    const { body, footer } = frame("Cargar seguimientos desde una planilla");
    body.appendChild(
      node("p", {
        className: "dispatch-hint",
        text: "Subí la planilla que te mandó el transportista, con el número de pedido y el de seguimiento. Se cargan todos de una vez y, si el pedido vino de una tienda, el comprador recibe el seguimiento.",
      })
    );
    const drop = node("label", { className: "file-drop" });
    const input = node("input", { attrs: { type: "file", accept: ".xlsx,.csv,.txt" } });
    drop.append(node("strong", { text: "Elegí el archivo o soltalo acá" }), node("span", { text: "Excel (.xlsx) o CSV" }), input);
    input.addEventListener("change", () => input.files[0] && read(input.files[0]));
    drop.addEventListener("dragover", (event) => {
      event.preventDefault();
      drop.classList.add("over");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("over"));
    drop.addEventListener("drop", (event) => {
      event.preventDefault();
      drop.classList.remove("over");
      if (event.dataTransfer.files[0]) read(event.dataTransfer.files[0]);
    });
    body.appendChild(drop);
    if (message) errorLine(body, message);
    const cancel = node("button", { className: "btn btn-outline", text: "Cancelar", attrs: { type: "button" } });
    cancel.onclick = close;
    footer.appendChild(cancel);
  }

  async function read(newFile, { mapping = null, store = "" } = {}) {
    file = newFile;
    const { body } = frame("Cargar seguimientos desde una planilla");
    body.appendChild(node("p", { text: `Leyendo ${file.name}…` }));
    const form = new FormData();
    form.append("file", file);
    if (store) form.append("store", store);
    if (mapping) form.append("mapping", JSON.stringify(mapping));
    try {
      // Sin Content-Type: el navegador arma el multipart con su boundary.
      const response = await apiFetch(PREVIEW_URL, { method: "POST", body: form });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo leer la planilla."));
      preview = { ...data, store };
      await renderReview();
    } catch (err) {
      if (err.isSessionExpired) return;
      renderPick(err.message || "No se pudo leer la planilla.");
    }
  }

  // ---------------------------------------------------------------------------
  // Paso 2: revisar y confirmar
  // ---------------------------------------------------------------------------
  function mappingEditor(open) {
    const details = node("details", { className: "mapping-editor" });
    details.open = open;
    details.appendChild(node("summary", { text: open ? "Elegí qué columna es cada dato" : "Cambiar las columnas" }));
    const row = node("div", { className: "dispatch-options" });
    MAPPING_FIELDS.forEach(([field, label]) => {
      const wrapper = node("label", { className: "dispatch-field", text: label });
      const select = node("select", { attrs: { "data-field": field } });
      const none = node("option", { text: "— ninguna —" });
      none.value = "";
      select.appendChild(none);
      (preview.headers || []).forEach((header) => {
        const option = node("option", { text: header });
        option.value = header;
        select.appendChild(option);
      });
      select.value = (preview.mapping || {})[field] || "";
      wrapper.appendChild(select);
      row.appendChild(wrapper);
    });
    const apply = node("button", { className: "btn btn-outline btn-small", text: "Volver a leer con estas columnas", attrs: { type: "button" } });
    apply.onclick = () => {
      const mapping = {};
      details.querySelectorAll("select[data-field]").forEach((select) => {
        mapping[select.dataset.field] = select.value || null;
      });
      read(file, { mapping, store: preview.store });
    };
    details.append(row, apply);
    return details;
  }

  async function loadStores() {
    if (stores) return stores;
    try {
      const response = await apiFetch(`${API_BASE}/integrations/stores/`);
      stores = response.ok ? extractResults(await response.json()) : [];
    } catch (err) {
      stores = [];
    }
    return stores;
  }

  function cell(text, className) {
    const td = node("td", { text: text ?? "" });
    if (className) td.className = className;
    return td;
  }

  function rowsTable(rows) {
    const wrap = node("div", { className: "tracking-table-wrap" });
    const table = node("table", { className: "tracking-table" });
    const headRow = node("tr");
    ["", "Fila", "Pedido", "Seguimiento", "Resultado"].forEach((text) => headRow.appendChild(node("th", { text })));
    const head = node("thead");
    head.appendChild(headRow);
    const body = node("tbody");
    rows.forEach((row, index) => {
      const tr = node("tr", { className: row.state === "ok" ? "row-ok" : "row-error" });
      const checkTd = node("td");
      if (row.state === "ok") {
        const checkbox = node("input", { attrs: { type: "checkbox", "data-index": String(index) } });
        checkbox.checked = true;
        checkbox.addEventListener("change", updateConfirm);
        checkTd.appendChild(checkbox);
      }
      tr.appendChild(checkTd);
      tr.appendChild(cell(row.row));
      const order = row.order;
      let match = row.order_key ? `#${row.order_key}` : "—";
      if (order) match = [`#${order.number}`, order.recipient, order.store_name].filter(Boolean).join(" · ");
      tr.appendChild(cell(match));
      let tracking = row.tracking_number || "—";
      if (order?.tracking_number && order.tracking_number !== row.tracking_number) tracking += ` (reemplaza ${order.tracking_number})`;
      tr.appendChild(cell(tracking));
      const result = cell(STATE_LABELS[row.state] || row.state, `state state-${row.state}`);
      if (row.message) {
        result.appendChild(node("br"));
        result.appendChild(node("small", { text: row.message }));
      }
      tr.appendChild(result);
      body.appendChild(tr);
    });
    table.append(head, body);
    wrap.appendChild(table);
    return wrap;
  }

  function selectedRows() {
    return [...dialog().querySelectorAll("tbody input[type=checkbox]:checked")].map((box) => preview.rows[Number(box.dataset.index)]);
  }

  function updateConfirm() {
    const button = document.getElementById("trackingConfirm");
    if (!button) return;
    const count = selectedRows().length;
    button.disabled = !count;
    button.textContent = count === 1 ? "Cargar 1 seguimiento" : `Cargar ${count} seguimientos`;
  }

  async function renderReview() {
    const { body, footer } = frame(`Seguimientos de ${file.name}`);
    const mapping = preview.mapping || {};
    const cancel = node("button", { className: "btn btn-outline", text: "Cancelar", attrs: { type: "button" } });
    cancel.onclick = close;
    const other = node("button", { className: "btn-link dispatch-manual", text: "Elegir otro archivo", attrs: { type: "button" } });
    other.onclick = () => renderPick();

    if (!mapping.order || !mapping.tracking_number) {
      errorLine(body, "No se reconoció la columna del número de pedido o la del seguimiento.");
      body.appendChild(mappingEditor(true));
      footer.append(other, cancel);
      return;
    }

    const rows = preview.rows || [];
    const counts = preview.counts || {};
    const chips = node("div", { className: "counts-row" });
    chips.appendChild(node("span", { className: "count-chip count-ok", text: `${counts.ok || 0} de ${rows.length} listas para cargar` }));
    Object.entries(counts).forEach(([state, count]) => {
      if (state !== "ok") chips.appendChild(node("span", { className: "count-chip count-error", text: `${count} · ${STATE_LABELS[state] || state}` }));
    });
    body.appendChild(chips);

    // Mismo número de pedido en dos tiendas: se pregunta de cuál son.
    if (counts.ambiguous) {
      const options = await loadStores();
      if (options.length > 1) {
        const field = node("label", { className: "dispatch-field", text: "Hay números de pedido repetidos en tus tiendas: ¿de cuál es esta planilla?" });
        const select = node("select");
        [["", "Elegí la tienda"], ...options.map((store) => [String(store.id), store.name || `Tienda ${store.external_store_id}`]), ["manual", "Pedidos cargados a mano"]].forEach(
          ([value, label]) => {
            const option = node("option", { text: label });
            option.value = value;
            select.appendChild(option);
          }
        );
        select.value = preview.store || "";
        select.addEventListener("change", () => read(file, { mapping, store: select.value }));
        field.appendChild(select);
        body.appendChild(field);
      }
    }

    body.appendChild(rowsTable(rows));

    const options = node("div", { className: "dispatch-options" });
    options.style.marginTop = "12px";
    // La planilla no dice el transportista: se pregunta una vez para todas.
    if (!mapping.carrier) {
      const carrierField = node("label", { className: "dispatch-field", text: "Transportista" });
      const carrierInput = node("input", { attrs: { id: "trackingCarrier", type: "text", maxlength: "100", placeholder: "Ej.: Correo Argentino" } });
      carrierField.appendChild(carrierInput);
      options.appendChild(carrierField);
    }
    const statusField = node("label", { className: "dispatch-field", text: "Dejar los pedidos como" });
    const statusSelect = node("select", { attrs: { id: "trackingStatus" } });
    [["dispatched", "Despachados"], ["in_transit", "En tránsito"], ["delivered", "Entregados"]].forEach(([value, label]) => {
      const option = node("option", { text: label });
      option.value = value;
      statusSelect.appendChild(option);
    });
    statusField.appendChild(statusSelect);
    options.appendChild(statusField);
    body.appendChild(options);
    body.appendChild(node("p", { className: "dispatch-hint", text: "Un pedido que ya está más adelante conserva su estado: solo se le carga el seguimiento." }));
    body.appendChild(mappingEditor(false));
    body.appendChild(node("p", { className: "dispatch-total", attrs: { id: "trackingResult" } }));

    const confirm = node("button", { className: "btn btn-primary", attrs: { type: "button", id: "trackingConfirm" } });
    confirm.onclick = confirmImport;
    footer.append(other, cancel, confirm);
    updateConfirm();
  }

  async function confirmImport() {
    const rows = selectedRows();
    if (!rows.length) return;
    const fallbackCarrier = document.getElementById("trackingCarrier")?.value.trim() || "";
    const payload = {
      status: document.getElementById("trackingStatus").value,
      rows: rows.map((row) => {
        const item = { order_id: row.order.id, tracking_number: row.tracking_number };
        const carrier = row.carrier || fallbackCarrier;
        if (carrier) item.carrier = carrier;
        if (row.tracking_url) item.tracking_url = row.tracking_url;
        return item;
      }),
    };
    const button = document.getElementById("trackingConfirm");
    button.disabled = true;
    button.textContent = "Cargando…";
    try {
      const response = await apiFetch(CONFIRM_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar los seguimientos."));
      changed = Boolean(data.updated);
      renderDone(data, rows.length);
    } catch (err) {
      if (err.isSessionExpired) return;
      const result = document.getElementById("trackingResult");
      result.textContent = err.message || "No se pudieron cargar los seguimientos.";
      result.classList.add("error");
      updateConfirm();
    }
  }

  function renderDone(data, total) {
    const { body, footer } = frame("Seguimientos cargados");
    body.appendChild(
      node("p", {
        className: data.updated ? "dispatch-success" : "dispatch-error",
        text: data.failed ? `Se cargaron ${data.updated} de ${total} seguimiento(s).` : `Listo: se cargaron ${data.updated} seguimiento(s).`,
      })
    );
    const failures = node("div");
    renderBulkResult(failures, data);
    body.appendChild(failures);
    const done = node("button", { className: "btn btn-primary", text: "Cerrar", attrs: { type: "button" } });
    done.onclick = close;
    footer.appendChild(done);
  }

  function open() {
    file = null;
    preview = null;
    changed = false;
    renderPick();
    dialog().showModal();
  }

  dialog()?.addEventListener("close", afterClose);
  document.getElementById("trackingImportBtn")?.addEventListener("click", open);

  return { open };
})();
