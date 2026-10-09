// El envío de cada pedido en "Mis pedidos" (pedidos.html): con qué transportista
// salió, su número y link de seguimiento, la impresión (rótulo + etiqueta de Andreani) y la selección de
// pedidos para despacharlos juntos. El diálogo de despacho es pedidos/despacho.js.
//
// Hoy el único transportista con API es Andreani (backend:
// /api/v1/carriers/andreani/, apps/carriers/andreani/). Con la cuenta conectada,
// despachar crea el envío y el pedido queda con transportista, número y link
// cargados solos; sin ella, el diálogo ofrece conectarla o cargar el
// seguimiento a mano (despachar.html).
//
// Todo texto que viene de un pedido o de Andreani se inserta con textContent.
// apiFetch sale de auth.js; showMessage, getErrorMessage y downloadResponse, de utils.js.

window.OrderShipping = (() => {
  const ANDREANI_API = `${window.APP_CONFIG.API_BASE}/carriers/andreani`;
  const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

  // null = todavía no se sabe; {allowed: false} = el rol no despacha por API.
  let account = null;
  let shipmentsByOrder = new Map();
  let ordersById = new Map();
  const selected = new Set();
  let reloadOrders = async () => {};

  function el(id) {
    return document.getElementById(id);
  }

  function button(text, className, handler) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = className;
    node.textContent = text;
    node.addEventListener("click", () => handler(node));
    return node;
  }

  // ---------------------------------------------------------------------------
  // Cuenta de Andreani
  // ---------------------------------------------------------------------------
  async function loadAccount() {
    try {
      const response = await apiFetch(`${ANDREANI_API}/account/`);
      account = response.ok ? { allowed: true, ...(await response.json()) } : { allowed: false };
    } catch (err) {
      if (err.isSessionExpired) return;
      account = { allowed: false };
    }
    renderStrip();
  }

  // Lista para despachar con un clic: cuenta cargada, completa y aceptada.
  function accountReady() {
    return Boolean(account?.allowed && account.exists && !account.missing?.length && !account.last_error);
  }

  function renderStrip() {
    // Mismo permiso (orders.create) que cargar seguimientos desde planilla.
    el("trackingImportBtn").hidden = !account?.allowed;
    const strip = el("carrierStrip");
    if (!account?.allowed) {
      strip.hidden = true;
      return;
    }
    strip.replaceChildren();
    const text = document.createElement("span");
    const links = document.createElement("span");
    links.className = "carrier-strip-links";
    const link = (href, label) => {
      const a = document.createElement("a");
      a.href = href;
      a.textContent = label;
      return a;
    };
    if (accountReady()) {
      strip.className = "carrier-strip ok";
      text.textContent = "Andreani conectada: al despachar se crea el envío y el seguimiento se carga solo.";
      links.append(link("andreani.html", "Cuenta"), link("checkout_andreani.html", "Precio en el checkout"));
    } else {
      strip.className = "carrier-strip";
      text.textContent = !account.exists
        ? "Conectá tu cuenta de Andreani para despachar con un clic."
        : account.last_error
          ? `Andreani rechazó tu cuenta: ${account.last_error}`
          : `A tu cuenta de Andreani le falta: ${account.missing.join(", ")}.`;
      links.append(link("andreani.html", account.exists ? "Completar cuenta" : "Conectar Andreani"));
    }
    strip.append(text, links);
    strip.hidden = false;
  }

  // ---------------------------------------------------------------------------
  // Envíos de los pedidos de la página
  // ---------------------------------------------------------------------------
  async function prepare(orders) {
    ordersById = new Map(orders.map((order) => [order.id, order]));
    shipmentsByOrder = new Map();
    if (account && !account.allowed) return;
    try {
      const response = await apiFetch(`${ANDREANI_API}/shipments/`);
      if (!response.ok) return;
      // Vienen del más nuevo al más viejo: por pedido queda el vigente (o el
      // último, si todos están cancelados).
      for (const shipment of (await response.json()).results || []) {
        const current = shipmentsByOrder.get(shipment.order_id);
        if (!current || (current.status === "cancelled" && shipment.status !== "cancelled")) {
          shipmentsByOrder.set(shipment.order_id, shipment);
        }
      }
    } catch (err) {
      if (err.isSessionExpired) return;
    }
  }

  function activeShipment(orderId) {
    const shipment = shipmentsByOrder.get(orderId);
    return shipment && shipment.status !== "cancelled" ? shipment : null;
  }

  function isSelectable(order) {
    return Boolean(order.is_shippable || activeShipment(order.id));
  }

  // Lo que ve el comerciante del envío: transportista, número con su link y el
  // último estado que informó el transportista.
  function shippingLine(order, shipment) {
    const line = document.createElement("p");
    line.className = "shipping-line";
    const carrier = document.createElement("strong");
    carrier.textContent = order.carrier || "Envío";
    line.appendChild(carrier);
    if (order.tracking_number) {
      line.appendChild(document.createTextNode(" · "));
      const number = document.createElement(order.tracking_url ? "a" : "span");
      number.textContent = order.tracking_number;
      if (order.tracking_url) {
        number.href = order.tracking_url;
        number.target = "_blank";
        number.rel = "noopener noreferrer";
      }
      line.appendChild(number);
    }
    if (shipment) {
      const state = document.createElement("span");
      state.className = `shipment-state ${shipment.status}`;
      state.textContent = shipment.carrier_status ? `${shipment.status_label} — ${shipment.carrier_status}` : shipment.status_label;
      line.append(" ", state);
      if (shipment.last_error) {
        const error = document.createElement("small");
        error.className = "shipment-error";
        error.textContent = shipment.last_error;
        line.append(" ", error);
      }
    }
    return line;
  }

  function decorate(order, item) {
    const shipment = activeShipment(order.id);
    if (isSelectable(order)) {
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "order-select";
      checkbox.checked = selected.has(order.id);
      checkbox.setAttribute("aria-label", `Elegir el pedido ${order.external_number || order.id}`);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selected.add(order.id);
        else selected.delete(order.id);
        item.classList.toggle("selected", checkbox.checked);
        renderSelectionBar();
      });
      item.classList.toggle("selected", checkbox.checked);
      item.querySelector(".order-item-header").prepend(checkbox);
    }
    if (order.carrier || order.tracking_number || shipment) {
      item.querySelector(".order-item-header").after(shippingLine(order, shipment));
    }

    const footer = item.querySelector(".order-item-footer");
    const actions = [];
    if (shipment) {
      actions.push(button("Imprimir", "btn btn-outline btn-small", (node) => downloadLabels([shipment], node)));
      actions.push(button("Actualizar seguimiento", "btn btn-outline btn-small", (node) => refreshShipment(shipment, node)));
      if (shipment.status === "pending") {
        actions.push(button("Cancelar envío", "btn-danger-text", (node) => cancelShipment(shipment, node)));
      }
    } else if (order.is_shippable) {
      actions.push(button("Despachar", "btn btn-primary btn-small", () => window.OrderDispatch.open([order])));
    }
    footer.prepend(...actions);
  }

  // ---------------------------------------------------------------------------
  // Selección
  // ---------------------------------------------------------------------------
  function selectedOrders() {
    return [...selected].map((id) => ordersById.get(id)).filter(Boolean);
  }

  function renderSelectionBar() {
    // La selección vale para la página que se ve.
    for (const id of [...selected]) if (!ordersById.has(id)) selected.delete(id);
    const orders = selectedOrders();
    const toDispatch = orders.filter((order) => order.is_shippable && !activeShipment(order.id));
    const withLabel = orders.filter((order) => activeShipment(order.id));
    el("selectionBar").hidden = !orders.length;
    el("selectionCount").textContent = orders.length === 1 ? "1 pedido elegido" : `${orders.length} pedidos elegidos`;
    el("dispatchSelectedBtn").hidden = !toDispatch.length;
    el("dispatchSelectedBtn").textContent = `Despachar (${toDispatch.length})`;
    el("labelsSelectedBtn").hidden = !withLabel.length;
    el("labelsSelectedBtn").textContent = `Imprimir (${withLabel.length})`;
    el("manifestSelectedBtn").textContent = `Planilla de retiro (${orders.length})`;
  }

  function clearSelection() {
    selected.clear();
    document.querySelectorAll(".order-select").forEach((box) => {
      box.checked = false;
      box.closest(".order-item")?.classList.remove("selected");
    });
    renderSelectionBar();
  }

  // ---------------------------------------------------------------------------
  // Imprimir, seguimiento y cancelación
  // ---------------------------------------------------------------------------
  // Un solo PDF para mandar a la impresora: por cada envío, nuestro rótulo
  // (con el número de Andreani) y después la etiqueta de Andreani.
  async function downloadLabels(shipments, node) {
    if (node) node.disabled = true;
    try {
      const response = await apiFetch(`${ANDREANI_API}/shipments/print/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shipment_ids: shipments.map((shipment) => shipment.id) }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(getErrorMessage(data, "No se pudo armar el PDF para imprimir."));
      }
      await downloadResponse(
        response,
        shipments.length === 1 ? `envio-${shipments[0].tracking_number}.pdf` : `envios-${shipments.length}.pdf`
      );
    } catch (err) {
      if (err.isSessionExpired) return;
      showMessage(err.message || "No se pudo armar el PDF para imprimir.");
    } finally {
      if (node) node.disabled = false;
    }
  }

  async function refreshShipment(shipment, node) {
    node.disabled = true;
    try {
      const response = await apiFetch(`${ANDREANI_API}/shipments/${shipment.id}/refresh/`, { method: "POST" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo consultar el seguimiento."));
      showMessage(`Envío ${data.tracking_number}: ${data.status_label}.`, "success");
      await reloadOrders();
    } catch (err) {
      if (err.isSessionExpired) return;
      showMessage(err.message || "No se pudo consultar el seguimiento.");
      node.disabled = false;
    }
  }

  async function cancelShipment(shipment, node) {
    if (!window.confirm(`¿Cancelar el envío ${shipment.tracking_number} en Andreani? El pedido queda sin seguimiento y se puede volver a despachar.`)) {
      return;
    }
    node.disabled = true;
    try {
      const response = await apiFetch(`${ANDREANI_API}/shipments/${shipment.id}/cancel/`, { method: "POST" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo cancelar el envío."));
      showMessage(`Envío ${shipment.tracking_number} cancelado.`, "success");
      await reloadOrders();
    } catch (err) {
      if (err.isSessionExpired) return;
      showMessage(err.message || "No se pudo cancelar el envío.");
      node.disabled = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Inicio
  // ---------------------------------------------------------------------------
  function init(options) {
    reloadOrders = async () => {
      await options.reloadOrders();
      renderSelectionBar();
    };
    el("dispatchSelectedBtn").addEventListener("click", () => {
      window.OrderDispatch.open(selectedOrders().filter((order) => order.is_shippable && !activeShipment(order.id)));
    });
    el("labelsSelectedBtn").addEventListener("click", (event) => {
      downloadLabels(selectedOrders().map((order) => activeShipment(order.id)).filter(Boolean), event.currentTarget);
    });
    el("clearSelectionBtn").addEventListener("click", clearSelection);
    loadAccount();
  }

  return {
    init,
    prepare,
    decorate,
    clearSelection,
    selectedOrders,
    downloadLabels,
    account: () => account,
    accountReady,
    reload: () => reloadOrders(),
  };
})();
