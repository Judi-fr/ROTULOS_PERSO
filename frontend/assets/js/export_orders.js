// Exportar pedidos a Excel o CSV (backend: GET /api/v1/orders/export/, ver
// apps.orders.bulk_views.OrderExportView). Exporta todo lo que coincide con
// los filtros, sin tildar pedidos: los mismos filtros que el listado.
//
// Sesión y apiFetch salen de auth.js; downloadResponse y los demás helpers,
// de utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

async function loadStoreFilter() {
  try {
    const response = await apiFetch(`${API_BASE}/integrations/stores/`);
    if (!response.ok) return;
    const stores = extractResults(await response.json());
    if (!stores.length) return;
    const select = document.getElementById("storeFilter");
    stores.forEach((store) => {
      const option = document.createElement("option");
      option.value = store.id;
      option.textContent = store.name || `Tienda ${store.external_store_id}`;
      select.appendChild(option);
    });
    const manual = document.createElement("option");
    manual.value = "manual";
    manual.textContent = "Pedidos cargados a mano";
    select.appendChild(manual);
    document.getElementById("storeFilterField").style.display = "";
  } catch (err) {
    if (!err.isSessionExpired) console.error("Error al cargar tiendas:", err);
  }
}

async function exportOrders() {
  const fileType = document.querySelector('input[name="fileType"]:checked').value;
  // El parámetro es file_type: DRF reserva ?format= para otra cosa.
  const params = new URLSearchParams({ file_type: fileType });
  const filters = {
    store: document.getElementById("storeFilter").value,
    status: document.getElementById("statusFilter").value,
    date_from: document.getElementById("dateFrom").value,
    date_to: document.getElementById("dateTo").value,
  };
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });

  const button = document.getElementById("exportBtn");
  button.disabled = true;
  button.textContent = "Exportando...";
  try {
    const response = await apiFetch(`${API_BASE}/orders/export/?${params}`);
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(getErrorMessage(data, "No se pudieron exportar los pedidos."));
    }
    const count = Number(response.headers.get("X-Order-Count") || "0");
    if (!count) {
      showMessage("No hay pedidos con estos filtros: no se descargó nada.");
      return;
    }
    await downloadResponse(response, `pedidos.${fileType}`);
    showMessage(`Se exportaron ${count} pedido(s).`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudieron exportar los pedidos.");
  } finally {
    button.disabled = false;
    button.textContent = "Exportar";
  }
}

document.getElementById("exportBtn").addEventListener("click", exportOrders);
document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

if (!window.Auth.getAccessToken()) {
  window.location.replace("index.html");
} else {
  loadStoreFilter();
  apiFetch(`${API_BASE}/auth/me/`)
    .then(async (response) => {
      if (response.ok) window.AppTopbar.render(await response.json());
    })
    .catch(() => {});
}
