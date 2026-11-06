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
  const payload = { order_ids: orderIds, status };
  const carrier = document.getElementById("carrierInput").value.trim();
  if (carrier && status !== "cancelled") payload.carrier = carrier;

  const button = document.getElementById("applyBtn");
  button.disabled = true;
  button.textContent = "Aplicando...";
  const resultBox = document.getElementById("bulkResult");
  resultBox.innerHTML = "";

  // En vez de "¿Seguro?": unos segundos con "Deshacer" antes de mandarlo
  // (appUndoable, utils.js). El cambio avisa a las tiendas y un pedido
  // cancelado no se reactiva, así que se manda recién cuando vence el aviso;
  // cancelar da más tiempo.
  const count = orderIds.length === 1 ? "1 pedido" : `${orderIds.length} pedidos`;
  const label = document.getElementById("targetStatus").selectedOptions[0]?.textContent.trim().toLowerCase() || status;
  try {
    const data = await appUndoable(
      status === "cancelled" ? `${orderIds.length === 1 ? "Se cancela" : "Se cancelan"} ${count}` : `${count}: ${label}`,
      async () => {
        const response = await apiFetch(`${API_BASE}/orders/bulk-status/`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(getErrorMessage(body, "No se pudieron actualizar los pedidos."));
        }
        return body;
      },
      { seconds: status === "cancelled" ? 10 : 6 }
    );
    if (data === false) {
      showMessage("Deshecho: los pedidos quedaron como estaban.", "info");
      return;
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

if (!window.Auth.hasSession()) {
  window.location.replace("index.html");
} else {
  picker = createOrderPicker(document.getElementById("orderPicker"));
  attachCarrierSuggestions(document.getElementById("carrierInput"));
  syncCarrierField();
  apiFetch(`${API_BASE}/auth/me/`)
    .then(async (response) => {
      if (response.ok) window.AppTopbar.render(await response.json());
    })
    .catch(() => {});
}
