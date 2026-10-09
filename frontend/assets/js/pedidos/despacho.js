// El diálogo "Despachar" de Mis pedidos (pedidos.html, <dialog id="dispatchDialog">).
//
// Con la cuenta de Andreani lista: muestra los pedidos con lo que cobra
// Andreani por cada uno (cotiza solo al abrir), y al confirmar crea los envíos
// (POST /carriers/andreani/shipments/). El pedido queda con transportista,
// número y link de seguimiento cargados; cuando Andreani lo retira, el
// seguimiento lo pasa a Despachado y se le avisa a la tienda. Al terminar
// ofrece imprimir: un PDF con nuestro rótulo y la etiqueta de Andreani de cada pedido.
//
// Sin cuenta lista: ofrece conectarla o, para un solo pedido, cargar el
// seguimiento a mano (despachar.html), que queda para transportistas sin API.
//
// Todo texto que viene de un pedido o de Andreani se inserta con textContent.

window.OrderDispatch = (() => {
  const API_BASE = window.APP_CONFIG.API_BASE;
  const ANDREANI_API = `${API_BASE}/carriers/andreani`;
  const apiFetch = (url, options) => window.Auth.apiFetch(url, options);
  const money = new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS" });

  let orders = [];
  let account = null;
  // Sucursal elegida por pedido (contratos de sucursal): {orderId: {id, name}}.
  let branches = {};
  let quoteRun = 0;
  let created = [];

  const dialog = () => document.getElementById("dispatchDialog");

  function node(tag, { className = "", text = "", attrs = {} } = {}) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
    return element;
  }

  function orderNumber(order) {
    return `#${order.external_number || order.id}`;
  }

  function orderSummary(order) {
    const address = order.address || {};
    return [address.recipient_name, address.city, address.postal_code ? `CP ${address.postal_code}` : ""].filter(Boolean).join(" · ");
  }

  // Si se creó algún envío, la lista se actualiza. Se llama al cerrar con
  // los botones y también desde el evento "close" (Esc): no repite.
  function afterClose() {
    if (!created.length) return;
    created = [];
    window.OrderShipping.clearSelection();
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
    header.append(
      node("h2", { text: title, attrs: { id: "dispatchTitle" } }),
      Object.assign(node("button", { className: "dispatch-close", text: "×", attrs: { type: "button", "aria-label": "Cerrar" } }), { onclick: close })
    );
    const body = node("div", { className: "dispatch-body" });
    const footer = node("div", { className: "dispatch-footer" });
    box.append(header, body, footer);
    return { body, footer };
  }

  function manualLink() {
    if (orders.length !== 1) return null;
    const link = node("a", { className: "dispatch-manual", text: "Otro transportista: cargar el seguimiento a mano" });
    link.href = `despachar.html?pedido=${encodeURIComponent(orders[0].id)}`;
    return link;
  }

  // ---------------------------------------------------------------------------
  // Sin cuenta lista
  // ---------------------------------------------------------------------------
  function renderNotReady() {
    const { body, footer } = frame(orders.length === 1 ? `Despachar ${orderNumber(orders[0])}` : `Despachar ${orders.length} pedidos`);
    let text;
    if (!account.allowed) text = "Tu usuario no puede despachar con un transportista.";
    else if (!account.exists) text = "Conectá tu cuenta de Andreani y despachá con un clic: el envío se crea solo y el pedido queda con su número y link de seguimiento.";
    else if (account.last_error) text = `Andreani rechazó tu cuenta: ${account.last_error}`;
    else text = `A tu cuenta de Andreani le falta: ${account.missing.join(", ")}.`;
    body.appendChild(node("p", { text }));
    const manual = manualLink();
    if (manual) footer.appendChild(manual);
    if (account.allowed) {
      const connect = node("a", { className: "btn btn-primary", text: account.exists ? "Completar la cuenta" : "Conectar Andreani" });
      connect.href = "andreani.html";
      footer.appendChild(connect);
    }
  }

  // ---------------------------------------------------------------------------
  // Con Andreani
  // ---------------------------------------------------------------------------
  function contracts() {
    return account.contracts || [];
  }

  function selectedContract() {
    const select = document.getElementById("dispatchContract");
    return contracts().find((contract) => contract.code === select?.value) || null;
  }

  function packageCount() {
    return Math.max(Number(document.getElementById("dispatchPackages")?.value) || 1, 1);
  }

  function renderReady() {
    const { body, footer } = frame(
      orders.length === 1 ? `Despachar ${orderNumber(orders[0])} con Andreani` : `Despachar ${orders.length} pedidos con Andreani`
    );

    const options = node("div", { className: "dispatch-options" });
    const contractField = node("label", { className: "dispatch-field", text: "Servicio" });
    const contractSelect = node("select", { attrs: { id: "dispatchContract" } });
    // Primero los de domicilio: es lo que se usa casi siempre.
    [...contracts()]
      .sort((a, b) => (a.kind === "home" ? 0 : 1) - (b.kind === "home" ? 0 : 1))
      .forEach((contract) => {
        const option = node("option", {
          text: `${contract.label || contract.code} (${contract.kind === "branch" ? "a sucursal / punto HOP" : "a domicilio"})`,
        });
        option.value = contract.code;
        contractSelect.appendChild(option);
      });
    contractField.appendChild(contractSelect);
    // Un solo contrato: no hay nada que elegir.
    contractField.hidden = contracts().length < 2;
    const packagesField = node("label", { className: "dispatch-field", text: "Bultos por pedido" });
    const packagesInput = node("input", { attrs: { id: "dispatchPackages", type: "number", min: "1", max: "20", value: "1" } });
    packagesField.appendChild(packagesInput);
    options.append(contractField, packagesField);

    const list = node("ul", { className: "dispatch-orders" });
    orders.forEach((order) => {
      const item = node("li", { attrs: { "data-order": order.id } });
      const info = node("div", { className: "dispatch-order-info" });
      info.append(node("strong", { text: orderNumber(order) }), node("span", { text: orderSummary(order) }));
      const price = node("span", { className: "dispatch-price", text: "…" });
      const branch = node("div", { className: "dispatch-branch", attrs: { hidden: "" } });
      item.append(info, price, branch);
      list.appendChild(item);
    });
    const total = node("p", { className: "dispatch-total", attrs: { id: "dispatchTotal" } });
    const hint = node("p", {
      className: "dispatch-hint",
      text: "Al confirmar se crean los envíos en Andreani y cada pedido queda con su número y link de seguimiento. Cuando Andreani lo retire, pasa a Despachado y se le avisa a la tienda.",
    });
    body.append(options, list, total, hint);

    const manual = manualLink();
    if (manual) footer.appendChild(manual);
    const cancel = node("button", { className: "btn btn-outline", text: "Cancelar", attrs: { type: "button" } });
    cancel.onclick = close;
    const confirm = node("button", {
      className: "btn btn-primary",
      text: orders.length === 1 ? "Crear envío" : `Crear ${orders.length} envíos`,
      attrs: { type: "button", id: "dispatchConfirm" },
    });
    confirm.onclick = createShipments;
    footer.append(cancel, confirm);

    contractSelect.addEventListener("change", onOptionsChange);
    packagesInput.addEventListener("change", onOptionsChange);
    onOptionsChange();
  }

  function onOptionsChange() {
    const contract = selectedContract();
    const isBranch = contract?.kind === "branch";
    dialog()
      .querySelectorAll(".dispatch-branch")
      .forEach((box) => {
        box.hidden = !isBranch;
      });
    if (isBranch) loadBranches();
    quote();
  }

  async function quote() {
    const run = ++quoteRun;
    const contract = selectedContract();
    const prices = dialog().querySelectorAll(".dispatch-price");
    prices.forEach((price) => {
      price.textContent = "Cotizando…";
      price.classList.remove("error");
    });
    const total = document.getElementById("dispatchTotal");
    total.textContent = "";
    if (!contract) return;
    try {
      const response = await apiFetch(`${ANDREANI_API}/shipments/quote/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ order_ids: orders.map((order) => order.id), contract: contract.code, package_count: packageCount() }),
      });
      const data = await response.json().catch(() => ({}));
      if (run !== quoteRun) return; // Cambiaron las opciones mientras cotizaba.
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo cotizar."));
      (data.results || []).forEach((result) => {
        const price = dialog().querySelector(`li[data-order="${CSS.escape(String(result.order_id))}"] .dispatch-price`);
        if (!price) return;
        price.textContent = result.ok ? money.format(Number(result.price)) : "Sin cotización";
        price.title = result.ok ? "Con IVA" : result.detail || "";
        price.classList.toggle("error", !result.ok);
      });
      if (data.quoted) total.textContent = `Total con IVA: ${money.format(Number(data.total))}`;
    } catch (err) {
      if (err.isSessionExpired || run !== quoteRun) return;
      // Sin precio igual se puede despachar: la cotización es informativa.
      prices.forEach((price) => {
        price.textContent = "—";
      });
      total.textContent = err.message || "No se pudo cotizar.";
    }
  }

  async function loadBranches() {
    const byPostalCode = {};
    for (const order of orders) {
      const box = dialog().querySelector(`li[data-order="${CSS.escape(String(order.id))}"] .dispatch-branch`);
      if (!box || box.dataset.loaded) continue;
      box.dataset.loaded = "1";
      const postalCode = order.address?.postal_code || "";
      const select = node("select");
      select.appendChild(node("option", { text: "Buscando sucursales…" }));
      box.replaceChildren(select);
      try {
        if (postalCode && !byPostalCode[postalCode]) {
          const response = await apiFetch(`${ANDREANI_API}/branches/?${new URLSearchParams({ cp: postalCode })}`);
          byPostalCode[postalCode] = response.ok ? (await response.json()).results || [] : [];
        }
      } catch (err) {
        if (err.isSessionExpired) return;
      }
      const found = byPostalCode[postalCode] || [];
      select.replaceChildren(node("option", { text: found.length ? "Elegí la sucursal o punto HOP" : "No hay sucursales para ese código postal" }));
      select.firstChild.value = "";
      found.forEach((branch) => {
        const option = node("option", { text: `${branch.is_hop ? "Punto HOP · " : ""}${branch.name} — ${branch.address}, ${branch.city}` });
        option.value = branch.id;
        option.dataset.name = branch.name;
        select.appendChild(option);
      });
      select.addEventListener("change", () => {
        const option = select.selectedOptions[0];
        if (option?.value) branches[order.id] = { id: option.value, name: option.dataset.name };
        else delete branches[order.id];
      });
    }
  }

  async function createShipments() {
    const contract = selectedContract();
    if (!contract) return;
    const confirm = document.getElementById("dispatchConfirm");
    confirm.disabled = true;
    confirm.textContent = "Creando…";
    const payload = { order_ids: orders.map((order) => order.id), contract: contract.code, package_count: packageCount() };
    if (contract.kind === "branch") payload.branches = branches;
    try {
      const response = await apiFetch(`${ANDREANI_API}/shipments/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron crear los envíos."));
      renderResult(data);
    } catch (err) {
      if (err.isSessionExpired) return;
      confirm.disabled = false;
      confirm.textContent = orders.length === 1 ? "Crear envío" : `Crear ${orders.length} envíos`;
      const total = document.getElementById("dispatchTotal");
      total.textContent = err.message || "No se pudieron crear los envíos.";
      total.classList.add("error");
    }
  }

  function renderResult(data) {
    created = (data.results || []).filter((result) => result.ok);
    const failed = (data.results || []).filter((result) => !result.ok);
    const { body, footer } = frame(created.length ? "Envíos creados" : "No se pudo despachar");
    if (created.length) {
      body.appendChild(
        node("p", {
          className: "dispatch-success",
          text:
            created.length === 1
              ? "Listo: el pedido ya tiene su número y link de seguimiento."
              : `Listo: ${created.length} pedidos ya tienen su número y link de seguimiento.`,
        })
      );
      const list = node("ul", { className: "dispatch-orders" });
      created.forEach((result) => {
        const item = node("li");
        const info = node("div", { className: "dispatch-order-info" });
        info.append(node("strong", { text: `#${result.number}` }), node("span", { text: result.recipient || "" }));
        const link = node("a", { text: result.tracking_number });
        link.href = result.tracking_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        item.append(info, link);
        list.appendChild(item);
      });
      body.appendChild(list);
    }
    if (failed.length) {
      body.appendChild(node("p", { className: "dispatch-error", text: failed.length === 1 ? "Este no se pudo despachar:" : "Estos no se pudieron despachar:" }));
      const list = node("ul", { className: "dispatch-failed" });
      failed.forEach((result) => list.appendChild(node("li", { text: `#${result.number ?? result.order_id}: ${result.detail}` })));
      body.appendChild(list);
    }
    const done = node("button", { className: created.length ? "btn btn-outline" : "btn btn-primary", text: "Cerrar", attrs: { type: "button" } });
    done.onclick = close;
    footer.appendChild(done);
    if (created.length) {
      const labels = node("button", {
        className: "btn btn-primary",
        text: created.length === 1 ? "Imprimir rótulo y etiqueta" : "Imprimir rótulos y etiquetas",
        attrs: { type: "button" },
      });
      labels.onclick = () => window.OrderShipping.downloadLabels(created, labels);
      footer.appendChild(labels);
    }
  }

  // ---------------------------------------------------------------------------
  // Abrir
  // ---------------------------------------------------------------------------
  async function open(selectedOrders) {
    orders = selectedOrders.filter(Boolean);
    if (!orders.length) return;
    branches = {};
    created = [];
    frame("Despachar").body.appendChild(node("p", { text: "Cargando…" }));
    dialog().showModal();
    // La cuenta se pide de nuevo: puede haberse conectado en otra pestaña.
    try {
      const response = await apiFetch(`${ANDREANI_API}/account/`);
      account = response.ok ? { allowed: true, ...(await response.json()) } : { allowed: false };
    } catch (err) {
      if (err.isSessionExpired) return;
      account = { allowed: false };
    }
    const ready = account.allowed && account.exists && !account.missing?.length && !account.last_error;
    if (ready) renderReady();
    else renderNotReady();
  }

  dialog()?.addEventListener("close", afterClose);

  return { open };
})();
