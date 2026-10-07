// Planilla de retiro (backend: POST /api/v1/orders/manifest/, ver
// apps.orders.manifest). El comerciante tilda los pedidos que se lleva el
// transportista y baja un PDF con la lista y el lugar para la firma.
// Opcionalmente los marca como despachados (POST /orders/bulk-status/).
//
// Sesión y apiFetch salen de auth.js; createOrderPicker, downloadResponse,
// renderBulkResult y los demás helpers, de utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

let picker = null;

async function generate() {
  const orderIds = picker.selectedIds();
  if (!orderIds.length) {
    showMessage("Elegí al menos un pedido.");
    return;
  }
  const carrier = document.getElementById("carrierInput").value.trim();
  const button = document.getElementById("generateBtn");
  button.disabled = true;
  button.textContent = "Generando...";
  document.getElementById("bulkResult").innerHTML = "";

  try {
    const response = await apiFetch(`${API_BASE}/orders/manifest/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ order_ids: orderIds, carrier }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(getErrorMessage(data, "No se pudo generar la planilla."));
    }
    await downloadResponse(response, "planilla-retiro.pdf");

    if (!document.getElementById("markDispatched").checked) {
      showMessage(`Planilla generada con ${orderIds.length} pedido(s).`, "success");
      return;
    }

    button.textContent = "Despachando...";
    const payload = { order_ids: orderIds, status: "dispatched" };
    if (carrier) payload.carrier = carrier;
    const shipResponse = await apiFetch(`${API_BASE}/orders/bulk-status/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await shipResponse.json().catch(() => ({}));
    if (!shipResponse.ok) {
      throw new Error(
        getErrorMessage(data, "La planilla se generó, pero no se pudieron despachar los pedidos.")
      );
    }
    renderBulkResult(document.getElementById("bulkResult"), data);
    showMessage(
      `Planilla generada. Se despacharon ${data.updated} de ${orderIds.length} pedido(s).`,
      data.failed ? "error" : "success"
    );
    picker.clear();
    picker.reload();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo generar la planilla.");
  } finally {
    button.disabled = false;
    button.textContent = "Generar planilla";
  }
}

document.getElementById("generateBtn").addEventListener("click", generate);
document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

if (!window.Auth.getAccessToken()) {
  window.location.replace("index.html");
} else {
  picker = createOrderPicker(document.getElementById("orderPicker"));
  apiFetch(`${API_BASE}/auth/me/`)
    .then(async (response) => {
      if (response.ok) window.AppTopbar.render(await response.json());
    })
    .catch(() => {});
}
