// Envíos por Andreani (envios_andreani.html). Backend: /api/v1/carriers/andreani/
// (apps/carriers/andreani/). Arriba se eligen pedidos (createOrderPicker de
// utils.js), se cotizan con el contrato elegido y se crean sus envíos; abajo, los envíos ya creados con su estado,
// etiquetas, actualizar y cancelar.
//
// Todo texto que viene de un pedido o de Andreani se inserta con textContent.
// Sesión y apiFetch salen de auth.js; showMessage, getErrorMessage,
// renderBulkResult, downloadResponse, formatDate y createOrderPicker, de utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const ANDREANI_API = `${API_BASE}/carriers/andreani`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

let account = null;
let picker = null;
// Sucursal elegida por pedido (contratos de sucursal): {orderId: {id, name}}.
const chosenBranches = {};

function el(id) {
  return document.getElementById(id);
}

function selectedContract() {
  return (account?.contracts || []).find((contract) => contract.code === el("contractSelect").value) || null;
}

function updateCreateButton() {
  const count = picker ? picker.selectedIds().length : 0;
  el("selectionCount").textContent = count === 1 ? "1 pedido elegido" : `${count} pedidos elegidos`;
  el("createBtn").disabled = !count || !selectedContract() || Boolean(account?.missing?.length);
  el("quoteBtn").disabled = !count || !selectedContract() || !account?.exists;
  el("branchSection").style.display = selectedContract()?.kind === "branch" ? "" : "none";
}

// ---------------------------------------------------------------------------
// Cuenta y contratos
// ---------------------------------------------------------------------------
async function loadAccount() {
  try {
    const response = await apiFetch(`${ANDREANI_API}/account/`);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    account = await response.json();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage("No se pudo cargar tu cuenta de Andreani.");
    return;
  }
  const select = el("contractSelect");
  select.innerHTML = "";
  (account.contracts || []).forEach((contract) => {
    const option = document.createElement("option");
    option.value = contract.code;
    const kind = contract.kind === "branch" ? "sucursal / punto HOP" : "domicilio";
    option.textContent = `${contract.label || contract.code} (${kind})`;
    select.appendChild(option);
  });
  const notice = el("accountNotice");
  const problems = [];
  if (!account.exists) problems.push("Todavía no cargaste tu cuenta de Andreani.");
  else if (account.missing?.length) problems.push(`A tu cuenta de Andreani le falta: ${account.missing.join(", ")}.`);
  if (account.last_error) problems.push(`Andreani rechazó tus credenciales: ${account.last_error}`);
  notice.textContent = problems.length ? `${problems.join(" ")} Completala en «Cuenta de Andreani».` : "";
  notice.style.display = problems.length ? "" : "none";
  updateCreateButton();
}

// ---------------------------------------------------------------------------
// Sucursales y puntos HOP (contratos de sucursal)
// ---------------------------------------------------------------------------
async function loadBranches() {
  const ids = picker.selectedIds();
  if (!ids.length) {
    showMessage("Elegí primero los pedidos.");
    return;
  }
  const button = el("loadBranchesBtn");
  button.disabled = true;
  button.textContent = "Buscando...";
  const rows = el("branchRows");
  rows.innerHTML = "";
  const byPostalCode = {};
  try {
    for (const id of ids) {
      const response = await apiFetch(`${API_BASE}/orders/${id}/`);
      if (!response.ok) continue;
      const order = await response.json();
      const postalCode = order.address?.postal_code || "";
      if (postalCode && !byPostalCode[postalCode]) {
        const branches = await apiFetch(`${ANDREANI_API}/branches/?${new URLSearchParams({ cp: postalCode })}`);
        byPostalCode[postalCode] = branches.ok ? (await branches.json()).results || [] : [];
      }
      rows.appendChild(branchRow(order, byPostalCode[postalCode] || []));
    }
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage("No se pudieron buscar las sucursales.");
  } finally {
    button.disabled = false;
    button.textContent = "Buscar sucursales de los pedidos elegidos";
  }
}

function branchRow(order, branches) {
  const row = document.createElement("div");
  row.className = "filters-row";
  const field = document.createElement("div");
  field.className = "field";
  const label = document.createElement("label");
  const recipient = order.address?.recipient_name || "";
  label.textContent = `#${order.external_number || order.id} · ${recipient} · CP ${order.address?.postal_code || "?"}`;
  const select = document.createElement("select");
  const none = document.createElement("option");
  none.value = "";
  none.textContent = branches.length ? "— elegí una sucursal o punto HOP —" : "No hay sucursales para ese código postal";
  select.appendChild(none);
  branches.forEach((branch) => {
    const option = document.createElement("option");
    option.value = branch.id;
    option.textContent = `${branch.is_hop ? "Punto HOP · " : ""}${branch.name} — ${branch.address}, ${branch.city}`;
    option.dataset.name = branch.name;
    select.appendChild(option);
  });
  const current = chosenBranches[order.id];
  if (current) select.value = current.id;
  select.addEventListener("change", () => {
    const option = select.selectedOptions[0];
    if (option && option.value) chosenBranches[order.id] = { id: option.value, name: option.dataset.name };
    else delete chosenBranches[order.id];
  });
  field.append(label, select);
  row.appendChild(field);
  return row;
}

// ---------------------------------------------------------------------------
// Cotizar
// ---------------------------------------------------------------------------
const money = new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS" });

function formatMoney(value) {
  return value === null || value === undefined || value === "" ? "—" : money.format(Number(value));
}

// Una cotización vale para la selección, el contrato y los bultos con que se
// pidió: si cambia algo, se borra para no mostrar un precio que ya no es.
function clearQuote() {
  el("quoteResult").innerHTML = "";
}

function renderQuote(data) {
  const container = el("quoteResult");
  container.innerHTML = "";
  const quoted = (data.results || []).filter((item) => item.ok);
  if (quoted.length) {
    const wrap = document.createElement("div");
    wrap.className = "tracking-table-wrap";
    const table = document.createElement("table");
    table.className = "tracking-table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    ["Pedido", "Destino", "Peso aforado", "Sin IVA", "Con IVA"].forEach((text) => {
      const th = document.createElement("th");
      th.textContent = text;
      headRow.appendChild(th);
    });
    head.appendChild(headRow);
    const body = document.createElement("tbody");
    quoted.forEach((item) => {
      const tr = document.createElement("tr");
      tr.className = "row-ok";
      tr.append(
        cell(`#${item.number}${item.recipient ? ` · ${item.recipient}` : ""}`),
        cell(item.postal_code ? `CP ${item.postal_code}` : "—"),
        cell(item.chargeable_weight_kg ? `${item.chargeable_weight_kg} kg` : "—"),
        cell(formatMoney(item.price_without_tax)),
        cell(formatMoney(item.price))
      );
      body.appendChild(tr);
    });
    const totalRow = document.createElement("tr");
    const label = cell(quoted.length === 1 ? "Total" : `Total (${quoted.length} envíos)`);
    label.colSpan = 4;
    const total = cell(formatMoney(data.total));
    label.style.fontWeight = total.style.fontWeight = "700";
    totalRow.append(label, total);
    body.appendChild(totalRow);
    table.append(head, body);
    wrap.appendChild(table);
    container.appendChild(wrap);
  }
  const failed = (data.results || []).filter((item) => !item.ok);
  if (failed.length) {
    const box = document.createElement("div");
    box.className = "bulk-result";
    const title = document.createElement("p");
    title.className = "bulk-result-title";
    title.textContent = failed.length === 1 ? "1 pedido no se pudo cotizar:" : `${failed.length} pedidos no se pudieron cotizar:`;
    const list = document.createElement("ul");
    failed.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = `#${item.number ?? item.order_id}: ${item.detail}`;
      list.appendChild(li);
    });
    box.append(title, list);
    container.appendChild(box);
  }
}

async function quoteShipments() {
  const ids = picker.selectedIds();
  const contract = selectedContract();
  if (!ids.length || !contract) return;
  const button = el("quoteBtn");
  button.disabled = true;
  button.textContent = "Cotizando...";
  clearQuote();
  try {
    const response = await apiFetch(`${ANDREANI_API}/shipments/quote/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order_ids: ids, contract: contract.code, package_count: Number(el("packageCount").value) || 1 }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo cotizar."));
    renderQuote(data);
    showMessage(
      data.failed
        ? `Se cotizaron ${data.quoted} de ${ids.length} pedido(s). Abajo, por qué no los demás.`
        : `Andreani cobra ${formatMoney(data.total)} con IVA por ${data.quoted} envío(s).`,
      data.quoted ? "success" : "error"
    );
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo cotizar.");
  } finally {
    button.textContent = "Cotizar";
    updateCreateButton();
  }
}

// ---------------------------------------------------------------------------
// Crear envíos
// ---------------------------------------------------------------------------
async function createShipments() {
  const ids = picker.selectedIds();
  const contract = selectedContract();
  if (!ids.length || !contract) return;
  const payload = {
    order_ids: ids,
    contract: contract.code,
    package_count: Number(el("packageCount").value) || 1,
  };
  if (contract.kind === "branch") payload.branches = chosenBranches;

  const button = el("createBtn");
  button.disabled = true;
  button.textContent = "Creando...";
  try {
    const response = await apiFetch(`${ANDREANI_API}/shipments/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron crear los envíos."));
    clearQuote();
    renderBulkResult(el("createResult"), data);
    showMessage(
      data.failed
        ? `Se crearon ${data.created} de ${ids.length} envío(s). Abajo, por qué no los demás.`
        : `Listo: se crearon ${data.created} envío(s). Bajá sus etiquetas en «Mis envíos Andreani».`,
      data.created ? "success" : "error"
    );
    picker.clear();
    picker.reload();
    await loadShipments();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudieron crear los envíos.");
  } finally {
    button.textContent = "Crear envíos";
    updateCreateButton();
  }
}

// ---------------------------------------------------------------------------
// Envíos creados
// ---------------------------------------------------------------------------
function cell(text) {
  const td = document.createElement("td");
  td.textContent = text ?? "";
  return td;
}

function actionButton(text, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn btn-outline btn-small";
  button.textContent = text;
  button.addEventListener("click", () => handler(button));
  return button;
}

function renderShipments(shipments) {
  const body = el("shipmentsBody");
  body.innerHTML = "";
  if (!shipments.length) {
    const tr = document.createElement("tr");
    const td = cell("Todavía no creaste envíos por Andreani.");
    td.colSpan = 8;
    tr.appendChild(td);
    body.appendChild(tr);
  }
  shipments.forEach((shipment) => {
    const tr = document.createElement("tr");
    tr.className = shipment.status === "issue" || shipment.status === "returning" ? "row-error" : "row-ok";
    const checkTd = document.createElement("td");
    if (shipment.status !== "cancelled") {
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = shipment.id;
      checkbox.addEventListener("change", updateLabelsButton);
      checkTd.appendChild(checkbox);
    }
    tr.appendChild(checkTd);

    const numberTd = document.createElement("td");
    const link = document.createElement("a");
    link.href = shipment.tracking_url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = shipment.tracking_number;
    numberTd.appendChild(link);
    tr.appendChild(numberTd);

    tr.appendChild(cell(`#${shipment.order_number}${shipment.recipient ? ` · ${shipment.recipient}` : ""}`));
    tr.appendChild(cell(shipment.delivery_kind === "branch" ? `Sucursal: ${shipment.branch_name || "—"}` : "A domicilio"));
    tr.appendChild(cell(formatMoney(shipment.quoted_price)));
    const statusTd = cell(shipment.status_label);
    if (shipment.last_error) {
      const detail = document.createElement("small");
      detail.textContent = shipment.last_error;
      statusTd.append(document.createElement("br"), detail);
    }
    tr.appendChild(statusTd);
    tr.appendChild(
      cell(shipment.carrier_status ? `${shipment.carrier_status}${shipment.last_event_at ? ` (${formatDate(shipment.last_event_at)})` : ""}` : "—")
    );

    const actions = document.createElement("td");
    if (shipment.status !== "cancelled") {
      actions.append(
        actionButton("Etiqueta", (button) => downloadLabel(shipment, button)),
        actionButton("Actualizar", (button) => refreshShipment(shipment, button))
      );
    }
    if (shipment.status === "pending") {
      actions.appendChild(actionButton("Cancelar", (button) => cancelShipment(shipment, button)));
    }
    tr.appendChild(actions);
    body.appendChild(tr);
  });
  el("selectAllShipments").checked = false;
  updateLabelsButton();
}

async function loadShipments() {
  try {
    const response = await apiFetch(`${ANDREANI_API}/shipments/`);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    renderShipments((await response.json()).results || []);
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage("No se pudieron cargar tus envíos.");
  }
}

function checkedShipmentIds() {
  return Array.from(el("shipmentsBody").querySelectorAll("input[type=checkbox]:checked")).map((box) => Number(box.value));
}

function updateLabelsButton() {
  const count = checkedShipmentIds().length;
  el("labelsBtn").disabled = !count;
  el("labelsBtn").textContent = count ? `Bajar etiquetas (${count})` : "Bajar etiquetas de los tildados";
}

async function downloadLabel(shipment, button) {
  button.disabled = true;
  try {
    const fileType = el("labelFormat").value;
    const response = await apiFetch(`${ANDREANI_API}/shipments/${shipment.id}/label/?file_type=${fileType}`);
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(getErrorMessage(data, "No se pudo bajar la etiqueta."));
    }
    await downloadResponse(response, `andreani-${shipment.tracking_number}.${fileType}`);
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo bajar la etiqueta.");
  } finally {
    button.disabled = false;
  }
}

async function downloadLabels() {
  const ids = checkedShipmentIds();
  if (!ids.length) return;
  const button = el("labelsBtn");
  button.disabled = true;
  try {
    const fileType = el("labelFormat").value;
    const response = await apiFetch(`${ANDREANI_API}/shipments/labels/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ shipment_ids: ids, file_type: fileType }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(getErrorMessage(data, "No se pudieron bajar las etiquetas."));
    }
    await downloadResponse(response, fileType === "zpl" ? "andreani-etiquetas.zpl" : "andreani-etiquetas.zip");
    showMessage(`Se bajaron ${ids.length} etiqueta(s).`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudieron bajar las etiquetas.");
  } finally {
    updateLabelsButton();
  }
}

async function refreshShipment(shipment, button) {
  button.disabled = true;
  try {
    const response = await apiFetch(`${ANDREANI_API}/shipments/${shipment.id}/refresh/`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo consultar el seguimiento."));
    showMessage(`Envío ${data.tracking_number}: ${data.status_label}.`, "success");
    await loadShipments();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo consultar el seguimiento.");
    button.disabled = false;
  }
}

async function cancelShipment(shipment, button) {
  if (!window.confirm(`¿Cancelar el envío ${shipment.tracking_number} en Andreani? El pedido queda sin seguimiento y se puede volver a enviar.`)) {
    return;
  }
  button.disabled = true;
  try {
    const response = await apiFetch(`${ANDREANI_API}/shipments/${shipment.id}/cancel/`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo cancelar el envío."));
    showMessage(`Envío ${shipment.tracking_number} cancelado.`, "success");
    await loadShipments();
    picker.reload();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo cancelar el envío.");
    button.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Inicio
// ---------------------------------------------------------------------------
el("contractSelect").addEventListener("change", () => {
  clearQuote();
  updateCreateButton();
});
el("packageCount").addEventListener("input", clearQuote);
el("quoteBtn").addEventListener("click", quoteShipments);
el("loadBranchesBtn").addEventListener("click", loadBranches);
el("createBtn").addEventListener("click", createShipments);
el("reloadShipmentsBtn").addEventListener("click", loadShipments);
el("labelsBtn").addEventListener("click", downloadLabels);
el("selectAllShipments").addEventListener("change", (event) => {
  el("shipmentsBody").querySelectorAll("input[type=checkbox]").forEach((box) => {
    box.checked = event.target.checked;
  });
  updateLabelsButton();
});
document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

if (!window.Auth.getAccessToken()) {
  window.location.replace("index.html");
} else {
  picker = createOrderPicker(el("pickerRoot"), {
    onSelectionChange: () => {
      clearQuote();
      updateCreateButton();
    },
  });
  loadAccount();
  loadShipments();
  apiFetch(`${API_BASE}/auth/me/`)
    .then(async (response) => {
      if (response.ok) window.AppTopbar.render(await response.json());
    })
    .catch(() => {});
}
