// El diálogo "Despachar" de Mis pedidos (pedidos.html, <dialog id="dispatchDialog">).
//
// Con al menos un transportista listo (Andreani, Correo Argentino, OCA: los dice
// GET /carriers/): se elige con cuál (si hay más de uno), el servicio y los
// bultos; muestra lo que cobra por cada pedido (cotiza solo al abrir, si ese
// transportista cotiza) y al confirmar crea los envíos (POST /carriers/shipments/).
// El pedido queda con transportista, número y link de seguimiento; cuando el
// transportista lo retira, el seguimiento lo pasa a Despachado y se le avisa a
// la tienda. Al terminar ofrece imprimir: un PDF con nuestro rótulo y la
// etiqueta del transportista de cada pedido.
//
// Sin ninguno listo: ofrece conectar uno o, para un solo pedido, cargar el
// seguimiento a mano (despachar.html), que queda para transportistas sin API.
//
// Todo texto que viene de un pedido o de un transportista se inserta con textContent.

window.OrderDispatch = (() => {
  const API_BASE = window.APP_CONFIG.API_BASE;
  const CARRIERS_API = `${API_BASE}/carriers`;
  const LAST_CARRIER_KEY = "dispatchCarrier";
  const apiFetch = (url, options) => window.Auth.apiFetch(url, options);
  const money = new Intl.NumberFormat("es-AR", { style: "currency", currency: "ARS" });

  let orders = [];
  // {allowed, carriers}: lo mismo que GET /carriers/.
  let account = null;
  let carrier = null;
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
  // Sin ningún transportista listo
  // ---------------------------------------------------------------------------
  function renderNotReady() {
    const { body, footer } = frame(orders.length === 1 ? `Despachar ${orderNumber(orders[0])}` : `Despachar ${orders.length} pedidos`);
    if (!account.allowed) {
      body.appendChild(node("p", { text: "Tu usuario no puede despachar con un transportista." }));
    } else {
      body.appendChild(
        node("p", {
          text: "Conectá tu cuenta de un transportista y despachá con un clic: el envío se crea solo y el pedido queda con su número y link de seguimiento.",
        })
      );
      const list = node("ul", { className: "dispatch-carriers" });
      account.carriers.forEach((item) => {
        const entry = node("li");
        const link = node("a", { text: item.account.exists ? `Completar la cuenta de ${item.name}` : `Conectar ${item.name}` });
        link.href = window.OrderShipping.accountPage(item);
        entry.appendChild(link);
        const problem = item.account.last_error
          ? `${item.name} rechazó tu cuenta: ${item.account.last_error}`
          : item.account.exists && item.account.missing?.length
            ? `Falta: ${item.account.missing.join(", ")}.`
            : "";
        if (problem) entry.appendChild(node("small", { text: ` ${problem}` }));
        list.appendChild(entry);
      });
      body.appendChild(list);
    }
    const manual = manualLink();
    if (manual) footer.appendChild(manual);
    const done = node("button", { className: "btn btn-outline", text: "Cerrar", attrs: { type: "button" } });
    done.onclick = close;
    footer.appendChild(done);
  }

  // ---------------------------------------------------------------------------
  // Con un transportista listo
  // ---------------------------------------------------------------------------
  function readyCarriers() {
    return (account.carriers || []).filter((item) => item.account.ready);
  }

  function contracts() {
    return carrier?.account.contracts || [];
  }

  function selectedContract() {
    const select = document.getElementById("dispatchContract");
    return contracts().find((contract) => contract.code === select?.value) || null;
  }

  function packageCount() {
    return Math.max(Number(document.getElementById("dispatchPackages")?.value) || 1, 1);
  }

  function renderReady() {
    const { body, footer } = frame(orders.length === 1 ? `Despachar ${orderNumber(orders[0])}` : `Despachar ${orders.length} pedidos`);

    const options = node("div", { className: "dispatch-options" });
    const carrierField = node("label", { className: "dispatch-field", text: "Transportista" });
    const carrierSelect = node("select", { attrs: { id: "dispatchCarrier" } });
    readyCarriers().forEach((item) => {
      const option = node("option", { text: item.name });
      option.value = item.code;
      carrierSelect.appendChild(option);
    });
    carrierSelect.value = carrier.code;
    carrierField.appendChild(carrierSelect);
    // Uno solo listo: no hay nada que elegir.
    carrierField.hidden = readyCarriers().length < 2;
    const contractField = node("label", { className: "dispatch-field", text: "Servicio", attrs: { id: "dispatchContractField" } });
    const contractSelect = node("select", { attrs: { id: "dispatchContract" } });
    contractField.appendChild(contractSelect);
    const packagesField = node("label", { className: "dispatch-field", text: "Bultos por pedido" });
    const packagesInput = node("input", { attrs: { id: "dispatchPackages", type: "number", min: "1", max: "20", value: "1" } });
    packagesField.appendChild(packagesInput);
    options.append(carrierField, contractField, packagesField);

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
    const hint = node("p", { className: "dispatch-hint", attrs: { id: "dispatchHint" } });
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

    carrierSelect.addEventListener("change", () => {
      carrier = readyCarriers().find((item) => item.code === carrierSelect.value) || carrier;
      try {
        localStorage.setItem(LAST_CARRIER_KEY, carrier.code);
      } catch (_err) {
        // Sin almacenamiento local se elige de nuevo la próxima vez.
      }
      onCarrierChange();
    });
    contractSelect.addEventListener("change", onOptionsChange);
    packagesInput.addEventListener("change", onOptionsChange);
    onCarrierChange();
  }

  // Cambió el transportista: sus servicios, su texto y las sucursales de nuevo.
  function onCarrierChange() {
    const contractSelect = document.getElementById("dispatchContract");
    contractSelect.replaceChildren();
    // Primero los de domicilio: es lo que se usa casi siempre.
    [...contracts()]
      .sort((a, b) => (a.kind === "home" ? 0 : 1) - (b.kind === "home" ? 0 : 1))
      .forEach((contract) => {
        const option = node("option", {
          text: `${contract.label || contract.code} (${contract.kind === "branch" ? `a ${carrier.branch_label}` : "a domicilio"})`,
        });
        option.value = contract.code;
        contractSelect.appendChild(option);
      });
    // Un solo servicio: no hay nada que elegir.
    document.getElementById("dispatchContractField").hidden = contracts().length < 2;
    document.getElementById("dispatchHint").textContent =
      `Al confirmar se crean los envíos en ${carrier.name} y cada pedido queda con su número y link de seguimiento. ` +
      `Cuando ${carrier.name} lo retire, pasa a Despachado y se le avisa a la tienda.`;
    branches = {};
    dialog()
      .querySelectorAll(".dispatch-branch")
      .forEach((box) => {
        delete box.dataset.loaded;
        box.replaceChildren();
      });
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
    total.classList.remove("error");
    if (!contract) return;
    // Sin cotizador (o sin sus credenciales) igual se despacha: el precio es informativo.
    const missing = carrier.supports_quote ? carrier.account.quote_missing || [] : ["un cotizador"];
    if (missing.length) {
      prices.forEach((price) => {
        price.textContent = "—";
      });
      total.textContent = carrier.supports_quote
        ? `Sin precio: a tu cuenta de ${carrier.name} le falta ${missing.join(", ")} para cotizar.`
        : "";
      return;
    }
    try {
      const response = await apiFetch(`${CARRIERS_API}/shipments/quote/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          carrier: carrier.code,
          order_ids: orders.map((order) => order.id),
          contract: contract.code,
          package_count: packageCount(),
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (run !== quoteRun) return; // Cambiaron las opciones mientras cotizaba.
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo cotizar."));
      (data.results || []).forEach((result) => {
        const price = dialog().querySelector(`li[data-order="${CSS.escape(String(result.order_id))}"] .dispatch-price`);
        if (!price) return;
        const days = result.delivery_days ? ` · ${result.delivery_days} ${result.delivery_days === 1 ? "día" : "días"}` : "";
        price.textContent = result.ok ? `${money.format(Number(result.price))}${days}` : "Sin cotización";
        price.title = result.ok ? "" : result.detail || "";
        price.classList.toggle("error", !result.ok);
      });
      if (data.quoted) total.textContent = `Total: ${money.format(Number(data.total))}`;
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
      const state = order.address?.state || "";
      const key = `${postalCode}|${state}`;
      const select = node("select");
      select.appendChild(node("option", { text: "Buscando sucursales…" }));
      box.replaceChildren(select);
      try {
        if (postalCode && !byPostalCode[key]) {
          const query = new URLSearchParams({ cp: postalCode, state });
          const response = await apiFetch(`${CARRIERS_API}/branches/${encodeURIComponent(carrier.code)}/?${query}`);
          byPostalCode[key] = response.ok ? (await response.json()).results || [] : [];
        }
      } catch (err) {
        if (err.isSessionExpired) return;
      }
      const found = byPostalCode[key] || [];
      select.replaceChildren(node("option", { text: found.length ? `Elegí la ${carrier.branch_label}` : "No hay sucursales para ese código postal" }));
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
    const payload = {
      carrier: carrier.code,
      order_ids: orders.map((order) => order.id),
      contract: contract.code,
      package_count: packageCount(),
    };
    if (contract.kind === "branch") payload.branches = branches;
    try {
      const response = await apiFetch(`${CARRIERS_API}/shipments/`, {
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
    // Las cuentas se piden de nuevo: puede haberse conectado una en otra pestaña.
    try {
      const response = await apiFetch(`${CARRIERS_API}/`);
      account = response.ok ? { allowed: true, carriers: (await response.json()).results || [] } : { allowed: false };
    } catch (err) {
      if (err.isSessionExpired) return;
      account = { allowed: false };
    }
    const ready = account.allowed ? readyCarriers() : [];
    if (!ready.length) {
      renderNotReady();
      return;
    }
    let last = "";
    try {
      last = localStorage.getItem(LAST_CARRIER_KEY) || "";
    } catch (_err) {
      last = "";
    }
    // El preferido de la cuenta (organizacion.html) si está listo; si no, el
    // último con el que se despachó; si no, el primero.
    const preferred = (await loadAccountSettings()).default_carrier || "";
    carrier =
      ready.find((item) => item.code === preferred) || ready.find((item) => item.code === last) || ready[0];
    renderReady();
  }

  dialog()?.addEventListener("close", afterClose);

  return { open };
})();
