// Cambio de estado masivo (backend: POST /api/v1/orders/bulk-status/, ver
// apps.orders.bulk_views.BulkStatusView). Mismas reglas que despachar o
// cancelar de a uno; el backend responde qué pasó con cada pedido.
//
// Sesión y apiFetch salen de auth.js; createOrderPicker, renderBulkResult y
// los demás helpers, de utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

let picker = null;

function syncCarrierField() {
  const cancelling = document.getElementById("targetStatus").value === "cancelled";
  document.getElementById("carrierField").style.display = cancelling ? "none" : "";
}

async function apply() {
  const orderIds = picker.selectedIds();
  if (!orderIds.length) {
    showMessage("Elegí al menos un pedido.");
    return;
  }
  const status = document.getElementById("targetStatus").value;
  // Cancelar no tiene vuelta atrás (un pedido cancelado no se reactiva):
  // se pide confirmación. Avanzar un estado se puede corregir hacia
  // adelante, así que no.
  if (
    status === "cancelled" &&
    !window.confirm(`¿Cancelar ${orderIds.length} pedido(s)? No se puede deshacer.`)
  ) {
    return;
  }

  const payload = { order_ids: orderIds, status };
  const carrier = document.getElementById("carrierInput").value.trim();
  if (carrier && status !== "cancelled") payload.carrier = carrier;

  const button = document.getElementById("applyBtn");
  button.disabled = true;
  button.textContent = "Aplicando...";
  const resultBox = document.getElementById("bulkResult");
  resultBox.innerHTML = "";

  try {
    const response = await apiFetch(`${API_BASE}/orders/bulk-status/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudieron actualizar los pedidos."));
    }
    renderBulkResult(resultBox, data);
    if (!data.failed) {
      showMessage(`Listo: ${data.updated} pedido(s) actualizado(s).`, "success");
    } else {
      showMessage(
        `Se actualizaron ${data.updated} de ${orderIds.length} pedido(s). Abajo, por qué no los demás.`,
        data.updated ? "success" : "error"
      );
    }
    picker.clear();
    picker.reload();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudieron actualizar los pedidos.");
  } finally {
    button.disabled = false;
    button.textContent = "Aplicar";
  }
}

document.getElementById("targetStatus").addEventListener("change", syncCarrierField);
document.getElementById("applyBtn").addEventListener("click", apply);
document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

if (!window.Auth.getAccessToken()) {
  window.location.replace("index.html");
} else {
  picker = createOrderPicker(document.getElementById("orderPicker"));
  syncCarrierField();
  apiFetch(`${API_BASE}/auth/me/`)
    .then(async (response) => {
      if (response.ok) window.AppTopbar.render(await response.json());
    })
    .catch(() => {});
}
