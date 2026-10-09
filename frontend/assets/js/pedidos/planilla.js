// "Planilla de retiro" en Mis pedidos (pedidos.html, <dialog id="manifestDialog">):
// la lista de los pedidos elegidos que se lleva el transportista, con lugar
// para su firma (backend: POST /orders/manifest/, apps.orders.manifest; cada
// pedido sale con su número de seguimiento).
//
// El transportista se completa solo si todos los pedidos elegidos salen con el
// mismo (por ejemplo, despachados con Andreani). Como entregarle los paquetes ES
// despacharlos, por defecto también los marca como despachados
// (POST /orders/bulk-status/); se puede destildar.
//
// Todo texto que viene de un pedido se inserta con textContent.

window.OrderManifest = (() => {
  const API_BASE = window.APP_CONFIG.API_BASE;
  const apiFetch = (url, options) => window.Auth.apiFetch(url, options);
  const dialog = () => document.getElementById("manifestDialog");

  let orders = [];
  let changed = false;

  function node(tag, { className = "", text = "", attrs = {} } = {}) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
    return element;
  }

  // Si se despachó algo, la lista se actualiza. Se llama al cerrar con los
  // botones y también desde el evento "close" (Esc): no repite.
  function afterClose() {
    if (!changed) return;
    changed = false;
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
    const closeButton = node("button", { className: "dispatch-close", text: "×", attrs: { type: "button", "aria-label": "Cerrar" } });
    closeButton.onclick = close;
    header.append(node("h2", { text: title, attrs: { id: "manifestTitle" } }), closeButton);
    const body = node("div", { className: "dispatch-body" });
    const footer = node("div", { className: "dispatch-footer" });
    box.append(header, body, footer);
    return { body, footer };
  }

  // El transportista de la planilla: el de los pedidos, si es uno solo.
  function commonCarrier() {
    const carriers = new Set(orders.map((order) => (order.carrier || "").trim()));
    return carriers.size === 1 ? [...carriers][0] : "";
  }

  // Ya despachados (o más allá) no se vuelven a marcar.
  function toDispatch() {
    return orders.filter((order) => ["created", "preparing"].includes(order.status));
  }

  function render() {
    const { body, footer } = frame(orders.length === 1 ? "Planilla de retiro (1 pedido)" : `Planilla de retiro (${orders.length} pedidos)`);
    body.appendChild(
      node("p", { className: "dispatch-hint", text: "La lista de los paquetes que se lleva el transportista, con su número de seguimiento y lugar para la firma." })
    );

    const carrierField = node("label", { className: "dispatch-field", text: "Transportista" });
    const carrierInput = node("input", {
      attrs: { id: "manifestCarrier", type: "text", maxlength: "100", placeholder: "Si lo dejás vacío, queda una línea para completar a mano" },
    });
    carrierInput.value = commonCarrier();
    carrierInput.style.width = "100%";
    carrierField.appendChild(carrierInput);
    body.appendChild(carrierField);

    const pending = toDispatch();
    if (pending.length) {
      const check = node("label", { className: "dispatch-check" });
      const box = node("input", { attrs: { id: "manifestMarkDispatched", type: "checkbox" } });
      box.checked = true;
      check.append(
        box,
        node("span", {
          text:
            pending.length === orders.length
              ? "Marcarlos como despachados (se le avisa a la tienda)"
              : `Marcar como despachados los ${pending.length} que todavía no lo están (se le avisa a la tienda)`,
        })
      );
      body.appendChild(check);
    }
    body.appendChild(node("p", { className: "dispatch-total", attrs: { id: "manifestResult" } }));

    const cancel = node("button", { className: "btn btn-outline", text: "Cancelar", attrs: { type: "button" } });
    cancel.onclick = close;
    const generate = node("button", { className: "btn btn-primary", text: "Generar planilla", attrs: { type: "button", id: "manifestGenerate" } });
    generate.onclick = run;
    footer.append(cancel, generate);
  }

  async function run() {
    const button = document.getElementById("manifestGenerate");
    const result = document.getElementById("manifestResult");
    const carrier = document.getElementById("manifestCarrier").value.trim();
    const markDispatched = Boolean(document.getElementById("manifestMarkDispatched")?.checked);
    button.disabled = true;
    button.textContent = "Generando…";
    result.textContent = "";
    result.classList.remove("error");
    try {
      const response = await apiFetch(`${API_BASE}/orders/manifest/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ order_ids: orders.map((order) => order.id), carrier }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(getErrorMessage(data, "No se pudo generar la planilla."));
      }
      await downloadResponse(response, "planilla-retiro.pdf");

      if (!markDispatched) {
        close();
        showMessage(`Planilla generada con ${orders.length} pedido(s).`, "success");
        return;
      }
      button.textContent = "Despachando…";
      const pending = toDispatch();
      const payload = { order_ids: pending.map((order) => order.id), status: "dispatched" };
      if (carrier) payload.carrier = carrier;
      const shipResponse = await apiFetch(`${API_BASE}/orders/bulk-status/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await shipResponse.json().catch(() => ({}));
      if (!shipResponse.ok) throw new Error(getErrorMessage(data, "La planilla se generó, pero no se pudieron marcar como despachados."));
      changed = true;
      close();
      showMessage(
        data.failed
          ? `Planilla generada. Se despacharon ${data.updated} de ${pending.length} pedido(s).`
          : `Planilla generada y ${data.updated} pedido(s) despachados.`,
        data.failed ? "error" : "success"
      );
    } catch (err) {
      if (err.isSessionExpired) return;
      result.textContent = err.message || "No se pudo generar la planilla.";
      result.classList.add("error");
      button.disabled = false;
      button.textContent = "Generar planilla";
    }
  }

  function open(selectedOrders) {
    orders = selectedOrders.filter(Boolean);
    if (!orders.length) return;
    changed = false;
    render();
    dialog().showModal();
  }

  dialog()?.addEventListener("close", afterClose);

  document.getElementById("manifestSelectedBtn")?.addEventListener("click", () => open(window.OrderShipping.selectedOrders()));

  return { open };
})();
