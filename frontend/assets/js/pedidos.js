// Direcciones y pedidos propios (self-service) para usuarios no administradores.
// Sesión y apiFetch salen de assets/js/auth.js (window.Auth), compartido con
// el resto del frontend (ver dashboard.js / perfil.js / usuarios.js).

const AUTH_BASE = `${window.APP_CONFIG.API_BASE}/auth`;
const API_BASE = window.APP_CONFIG.API_BASE;
const ME_URL = `${AUTH_BASE}/me/`;
const ADDRESSES_URL = `${API_BASE}/addresses/`;
const ORDERS_URL = `${API_BASE}/orders/`;
const STORES_URL = `${API_BASE}/integrations/stores/`;

const STATUS_LABELS = {
  created: "Creado",
  preparing: "En preparación",
  dispatched: "Despachado",
  in_transit: "En tránsito",
  delivered: "Entregado",
  cancelled: "Cancelado",
};

let addresses = [];
// Paginación del historial: el backend pagina (PageNumberPagination) y antes
// se mostraba solo la primera página, así que un pedido viejo no aparecía por
// ningún lado. Se navega con next/previous, que es lo que manda el backend.
let ordersPage = 1;
let ordersHasNext = false;

const getAccessToken = () => window.Auth.getAccessToken();
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

// showMessage, getErrorMessage y extractResults salen de assets/js/utils.js.
// formatDate queda propia acá abajo: esta página no muestra la hora, solo
// la fecha (a diferencia de la versión de utils.js).

function showFieldMessage(id, text, ok) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.style.color = ok ? "#16a34a" : "#dc2626";
  el.style.display = "block";
}

function formatDate(iso) {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleDateString("es-AR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
  } catch {
    return iso;
  }
}

const getCurrentUser = () => window.Auth.getCurrentUser();

// ---------------------------------------------------------------------------
// DIRECCIONES
// ---------------------------------------------------------------------------

function addressSummary(address) {
  const parts = [address.street, address.number].filter(Boolean).join(" ");
  const cityLine = [address.city, address.state].filter(Boolean).join(", ");
  return [parts, cityLine].filter(Boolean).join(" — ");
}

function renderAddressList() {
  const container = document.getElementById("addressList");
  if (!container) return;

  if (addresses.length === 0) {
    container.innerHTML = '<p class="empty-state">Todavía no agregaste ninguna dirección.</p>';
    return;
  }

  container.innerHTML = "";
  addresses.forEach((address) => {
    const item = document.createElement("div");
    item.className = "address-item";
    item.innerHTML = `
      <div class="address-item-info">
        <strong>${escapeHtml(address.label || "Dirección")}${address.is_default ? '<span class="badge-default">Predeterminada</span>' : ""}</strong>
        <p>${escapeHtml(addressSummary(address))}</p>
        ${address.reference ? `<p>${escapeHtml(address.reference)}</p>` : ""}
      </div>
      <div class="address-item-actions">
        ${address.is_default ? "" : `<button type="button" class="btn btn-outline btn-small" data-set-default="${escapeHtml(address.id)}">Predeterminada</button>`}
        <button type="button" class="btn-danger-text" data-delete-address="${escapeHtml(address.id)}">Eliminar</button>
      </div>
    `;
    container.appendChild(item);
  });
}

function renderAddressSelect() {
  const select = document.getElementById("orderAddressSelect");
  const noAddressMsg = document.getElementById("noAddressMsg");
  const newOrderForm = document.getElementById("newOrderForm");
  if (!select) return;

  if (addresses.length === 0) {
    select.innerHTML = "";
    if (noAddressMsg) noAddressMsg.style.display = "block";
    if (newOrderForm) newOrderForm.style.display = "none";
    return;
  }

  if (noAddressMsg) noAddressMsg.style.display = "none";
  if (newOrderForm) newOrderForm.style.display = "block";

  select.innerHTML = addresses
    .map(
      (address) =>
        `<option value="${escapeHtml(address.id)}">${escapeHtml(address.label || "Dirección")} — ${escapeHtml(addressSummary(address))}</option>`
    )
    .join("");
}

async function loadAddresses() {
  try {
    const response = await apiFetch(ADDRESSES_URL);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    const data = await response.json();
    addresses = extractResults(data);
    renderAddressList();
    renderAddressSelect();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar direcciones:", err);
    document.getElementById("addressList").innerHTML =
      '<p class="empty-state">No se pudieron cargar tus direcciones.</p>';
  }
}

function resetAddressForm() {
  ["addrLabel", "addrStreet", "addrNumber", "addrCity", "addrState", "addrPostalCode", "addrReference"].forEach(
    (id) => {
      const el = document.getElementById(id);
      if (el) el.value = "";
    }
  );
  const checkbox = document.getElementById("addrIsDefault");
  if (checkbox) checkbox.checked = false;
  const msg = document.getElementById("addressMsg");
  if (msg) msg.style.display = "none";
}

async function saveNewAddress() {
  const payload = {
    label: document.getElementById("addrLabel").value.trim(),
    street: document.getElementById("addrStreet").value.trim(),
    number: document.getElementById("addrNumber").value.trim(),
    city: document.getElementById("addrCity").value.trim(),
    state: document.getElementById("addrState").value.trim(),
    postal_code: document.getElementById("addrPostalCode").value.trim(),
    reference: document.getElementById("addrReference").value.trim(),
    is_default: document.getElementById("addrIsDefault").checked,
  };

  if (!payload.street || !payload.city) {
    showFieldMessage("addressMsg", "Completá al menos la calle y la ciudad.", false);
    return;
  }

  const button = document.getElementById("saveAddressBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Guardando...";

  try {
    const response = await apiFetch(ADDRESSES_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudo guardar la dirección."));
    }
    resetAddressForm();
    document.getElementById("addressForm").style.display = "none";
    await loadAddresses();
    showMessage("Dirección guardada correctamente.", "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showFieldMessage("addressMsg", err.message || "No se pudo guardar la dirección.", false);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

async function setDefaultAddress(id) {
  try {
    const response = await apiFetch(`${ADDRESSES_URL}${id}/`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_default: true }),
    });
    if (!response.ok) throw new Error("No se pudo actualizar la dirección predeterminada.");
    await loadAddresses();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo actualizar la dirección predeterminada.");
  }
}

async function deleteAddress(id) {
  const confirmed = window.confirm("¿Eliminar esta dirección?");
  if (!confirmed) return;

  try {
    const response = await apiFetch(`${ADDRESSES_URL}${id}/`, { method: "DELETE" });
    if (response.status === 204) {
      await loadAddresses();
      return;
    }
    const data = await response.json().catch(() => ({}));
    throw new Error(getErrorMessage(data, "No se pudo eliminar la dirección."));
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo eliminar la dirección.");
  }
}

// ---------------------------------------------------------------------------
// PEDIDOS
// ---------------------------------------------------------------------------

// Los pedidos que vienen de una tienda online traen texto escrito por
// terceros (productos, direcciones del comprador): todo valor que va a
// innerHTML se escapa. escapeHtml sale de assets/js/utils.js.

function renderOrderList(orders) {
  const container = document.getElementById("orderList");
  if (!container) return;

  if (orders.length === 0) {
    const filtered = document.getElementById("orderStoreFilter")?.value;
    container.innerHTML = filtered
      ? '<p class="empty-state">No hay pedidos para esta tienda.</p>'
      : '<p class="empty-state">Todavía no hiciste ningún pedido.</p>';
    return;
  }

  container.innerHTML = "";
  orders.forEach((order) => {
    const statusKey = order.status;
    const statusLabel = order.status_label || STATUS_LABELS[statusKey] || statusKey;
    const timeline = (order.status_events || [])
      .map((event) => `<span class="order-timeline-step">${escapeHtml(event.status_label || STATUS_LABELS[event.status] || event.status)}</span>`)
      .join("");

    // Acciones: despacho/seguimiento en su propia página (despachar.html).
    const actions = [];
    if (order.is_shippable) {
      actions.push(
        `<a class="btn btn-outline btn-small" href="despachar.html?pedido=${encodeURIComponent(order.id)}">Despachar / seguimiento</a>`
      );
    }
    if (order.is_cancellable) {
      actions.push(
        `<button type="button" class="btn btn-outline btn-small" data-cancel-order="${escapeHtml(order.id)}">Cancelar pedido</button>`
      );
    }

    // Pedido de tienda: se muestra el número que ve el comerciante en su
    // tienda (#1001) y de qué tienda viene; los manuales, su id propio.
    const number = order.external_number || order.id;
    const storeTag = order.store_connection
      ? `<span class="store-tag">${escapeHtml(order.store_name || "Tienda")}</span>`
      : "";

    const item = document.createElement("div");
    item.className = "order-item";
    item.innerHTML = `
      <div class="order-item-header">
        <div>
          <div class="order-item-title">Pedido #${escapeHtml(number)}${order.description ? ` — ${escapeHtml(order.description)}` : ""}${storeTag}</div>
          <p class="order-item-address">${order.address ? escapeHtml(addressSummary(order.address)) : "-"} · ${escapeHtml(formatDate(order.created_at))}</p>
        </div>
        <span class="status-badge ${escapeHtml(statusKey)}"><span class="dot"></span>${escapeHtml(statusLabel)}</span>
      </div>
      ${timeline ? `<div class="order-timeline">${timeline}</div>` : ""}
      ${actions.length ? `<div class="order-item-footer">${actions.join("")}</div>` : ""}
    `;
    container.appendChild(item);
  });
}

function renderOrdersPager(data) {
  ordersHasNext = Boolean(data.next);
  const pager = document.getElementById("ordersPager");
  if (!pager) return;
  pager.style.display = ordersHasNext || ordersPage > 1 ? "flex" : "none";
  document.getElementById("ordersPageInfo").textContent = `Página ${ordersPage}`;
  document.getElementById("ordersPrevBtn").disabled = ordersPage <= 1;
  document.getElementById("ordersNextBtn").disabled = !ordersHasNext;
}

async function loadOrders() {
  try {
    const store = document.getElementById("orderStoreFilter")?.value;
    const params = new URLSearchParams({ page: String(ordersPage) });
    if (store) params.set("store", store);
    const response = await apiFetch(`${ORDERS_URL}?${params.toString()}`);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    const data = await response.json();
    renderOrderList(extractResults(data));
    renderOrdersPager(data);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar pedidos:", err);
    document.getElementById("orderList").innerHTML =
      '<p class="empty-state">No se pudieron cargar tus pedidos.</p>';
  }
}

// Filtro por tienda: se arma con las tiendas conectadas del usuario (incluye
// desconectadas, porque sus pedidos siguen en el historial). Sin tiendas, o
// si el usuario no puede verlas, el filtro queda oculto.
async function loadStoreFilter() {
  const field = document.getElementById("orderStoreFilterField");
  const select = document.getElementById("orderStoreFilter");
  if (!field || !select) return;
  try {
    const response = await apiFetch(STORES_URL);
    if (!response.ok) return;
    const stores = extractResults(await response.json());
    if (stores.length === 0) return;

    stores.forEach((store) => {
      const option = document.createElement("option");
      option.value = store.id;
      option.textContent = store.status === "revoked" ? `${store.name} (desconectada)` : store.name;
      select.appendChild(option);
    });
    const manual = document.createElement("option");
    manual.value = "manual";
    manual.textContent = "Pedidos cargados a mano";
    select.appendChild(manual);

    select.addEventListener("change", () => {
      ordersPage = 1;
      loadOrders();
    });
    field.style.display = "";
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar tiendas:", err);
  }
}

async function createOrder() {
  const addressId = document.getElementById("orderAddressSelect").value;
  const description = document.getElementById("orderDescription").value.trim();

  if (!addressId) {
    showFieldMessage("orderFormMsg", "Elegí una dirección de envío.", false);
    return;
  }

  const button = document.getElementById("createOrderBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Creando...";

  try {
    const response = await apiFetch(ORDERS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address_id: addressId, description }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudo crear el pedido."));
    }
    document.getElementById("orderDescription").value = "";
    showFieldMessage("orderFormMsg", "Pedido creado correctamente.", true);
    await loadOrders();
  } catch (err) {
    if (err.isSessionExpired) return;
    showFieldMessage("orderFormMsg", err.message || "No se pudo crear el pedido.", false);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

async function cancelOrder(id) {
  const confirmed = window.confirm("¿Cancelar este pedido?");
  if (!confirmed) return;

  try {
    const response = await apiFetch(`${ORDERS_URL}${id}/cancel/`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudo cancelar el pedido."));
    }
    await loadOrders();
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo cancelar el pedido.");
  }
}

// ---------------------------------------------------------------------------
// Delegación de clicks (los items se re-crean al pintar las listas).
// ---------------------------------------------------------------------------
document.addEventListener("click", (e) => {
  const setDefaultBtn = e.target.closest("[data-set-default]");
  if (setDefaultBtn) {
    setDefaultAddress(setDefaultBtn.dataset.setDefault);
    return;
  }

  const deleteBtn = e.target.closest("[data-delete-address]");
  if (deleteBtn) {
    deleteAddress(deleteBtn.dataset.deleteAddress);
    return;
  }

  const cancelBtn = e.target.closest("[data-cancel-order]");
  if (cancelBtn) {
    cancelOrder(cancelBtn.dataset.cancelOrder);
  }
});

document.getElementById("toggleAddressFormBtn")?.addEventListener("click", () => {
  const form = document.getElementById("addressForm");
  form.style.display = form.style.display === "none" ? "block" : "none";
});
document.getElementById("cancelAddressFormBtn")?.addEventListener("click", () => {
  resetAddressForm();
  document.getElementById("addressForm").style.display = "none";
});
document.getElementById("saveAddressBtn")?.addEventListener("click", saveNewAddress);
document.getElementById("createOrderBtn")?.addEventListener("click", createOrder);
document.getElementById("ordersPrevBtn")?.addEventListener("click", () => {
  if (ordersPage > 1) {
    ordersPage -= 1;
    loadOrders();
  }
});
document.getElementById("ordersNextBtn")?.addEventListener("click", () => {
  if (ordersHasNext) {
    ordersPage += 1;
    loadOrders();
  }
});

// ---------------------------------------------------------------------------
// Logout: misma lógica que dashboard.js / perfil.js (window.Auth.logout).
// ---------------------------------------------------------------------------
const logoutBtn = document.getElementById("logoutBtn");
if (logoutBtn) {
  logoutBtn.addEventListener("click", () => window.Auth.logout());
}

async function init() {
  try {
    const response = await apiFetch(ME_URL);
    if (response.ok) {
      window.AppTopbar.render(await response.json());
    }
  } catch (err) {
    if (err.isSessionExpired) return;
  }
  await loadAddresses();
  await loadStoreFilter();
  await loadOrders();
}

if (!getAccessToken()) {
  window.location.replace("index.html");
} else {
  init();
}
