// Direcciones y pedidos propios (self-service) para usuarios no administradores.
// Sesión y apiFetch salen de assets/js/auth.js (window.Auth), compartido con
// el resto del frontend (ver dashboard.js / perfil.js / admingestion_test.js).

const AUTH_BASE = `${window.APP_CONFIG.API_BASE}/auth`;
const API_BASE = window.APP_CONFIG.API_BASE;
const ME_URL = `${AUTH_BASE}/me/`;
const ADDRESSES_URL = `${API_BASE}/addresses/`;
const ORDERS_URL = `${API_BASE}/orders/`;

const STATUS_LABELS = {
  created: "Creado",
  preparing: "En preparación",
  dispatched: "Despachado",
  in_transit: "En tránsito",
  delivered: "Entregado",
  cancelled: "Cancelado",
};

let addresses = [];

const getAccessToken = () => window.Auth.getAccessToken();
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

function showMessage(text, type = "error") {
  const el = document.getElementById("pageMessage");
  if (!el) return;
  el.textContent = text;
  el.className = `page-message ${type}`;
  el.style.display = "block";
}

function showFieldMessage(id, text, ok) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.style.color = ok ? "#16a34a" : "#dc2626";
  el.style.display = "block";
}

function getErrorMessage(data, fallback) {
  if (typeof data === "string") return data;
  if (!data || typeof data !== "object") return fallback;
  if (typeof data.detail === "string") return data.detail;
  for (const value of Object.values(data)) {
    if (Array.isArray(value) && value.length) return String(value[0]);
    if (typeof value === "string") return value;
  }
  return fallback;
}

// La API pagina (PageNumberPagination): {count, next, previous, results}.
// Se soporta también una lista simple por las dudas.
function extractResults(data) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.results)) return data.results;
  return [];
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

function renderTopbar(user) {
  const nameEl = document.getElementById("userName");
  const emailEl = document.getElementById("userEmail");
  const avatarEl = document.getElementById("userAvatar");

  const name = user.display_name || user.email || "Usuario";
  const email = user.email || "—";

  if (nameEl) nameEl.textContent = name;
  if (emailEl) emailEl.textContent = email;
  if (avatarEl) {
    const picture = getCurrentUser().picture;
    avatarEl.src = picture
      ? picture
      : `https://api.dicebear.com/7.x/avataaars/svg?seed=${encodeURIComponent(email)}`;
  }
}

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
        <strong>${address.label || "Dirección"}${address.is_default ? '<span class="badge-default">Predeterminada</span>' : ""}</strong>
        <p>${addressSummary(address)}</p>
        ${address.reference ? `<p>${address.reference}</p>` : ""}
      </div>
      <div class="address-item-actions">
        ${address.is_default ? "" : `<button type="button" class="btn btn-outline btn-small" data-set-default="${address.id}">Predeterminada</button>`}
        <button type="button" class="btn-danger-text" data-delete-address="${address.id}">Eliminar</button>
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
        `<option value="${address.id}">${address.label || "Dirección"} — ${addressSummary(address)}</option>`
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

function renderOrderList(orders) {
  const container = document.getElementById("orderList");
  if (!container) return;

  if (orders.length === 0) {
    container.innerHTML = '<p class="empty-state">Todavía no hiciste ningún pedido.</p>';
    return;
  }

  container.innerHTML = "";
  orders.forEach((order) => {
    const statusKey = order.status;
    const statusLabel = order.status_label || STATUS_LABELS[statusKey] || statusKey;
    const timeline = (order.status_events || [])
      .map((event) => `<span class="order-timeline-step">${event.status_label || STATUS_LABELS[event.status] || event.status}</span>`)
      .join("");

    const item = document.createElement("div");
    item.className = "order-item";
    item.innerHTML = `
      <div class="order-item-header">
        <div>
          <div class="order-item-title">Pedido #${order.id}${order.description ? ` — ${order.description}` : ""}</div>
          <p class="order-item-address">${order.address ? addressSummary(order.address) : "-"} · ${formatDate(order.created_at)}</p>
        </div>
        <span class="status-badge ${statusKey}"><span class="dot"></span>${statusLabel}</span>
      </div>
      ${timeline ? `<div class="order-timeline">${timeline}</div>` : ""}
      ${
        order.is_cancellable
          ? `<div class="order-item-footer"><button type="button" class="btn btn-outline btn-small" data-cancel-order="${order.id}">Cancelar pedido</button></div>`
          : ""
      }
    `;
    container.appendChild(item);
  });
}

async function loadOrders() {
  try {
    const response = await apiFetch(ORDERS_URL);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    const data = await response.json();
    renderOrderList(extractResults(data));
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar pedidos:", err);
    document.getElementById("orderList").innerHTML =
      '<p class="empty-state">No se pudieron cargar tus pedidos.</p>';
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
      renderTopbar(await response.json());
    }
  } catch (err) {
    if (err.isSessionExpired) return;
  }
  await loadAddresses();
  await loadOrders();
}

if (!getAccessToken()) {
  window.location.replace("index.html");
} else {
  init();
}
