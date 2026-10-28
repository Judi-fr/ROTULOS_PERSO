// Mis direcciones (direcciones.html): las direcciones propias del comercio,
// con las que se cargan pedidos a mano desde Mis pedidos. Antes era una
// sección de pedidos.html; tiene su página para que Mis pedidos sea solo la
// lista de pedidos. Sesión y apiFetch salen de assets/js/auth.js; showMessage,
// getErrorMessage y extractResults de assets/js/utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const ME_URL = `${API_BASE}/auth/me/`;
const ADDRESSES_URL = `${API_BASE}/addresses/`;

const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

let addresses = [];

function showFieldMessage(id, text, ok) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.style.color = ok ? "#16a34a" : "#dc2626";
  el.style.display = "block";
}

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
        ${address.is_default ? "" : `<button type="button" class="btn btn-outline btn-small" data-set-default="${escapeHtml(address.id)}">Usar como predeterminada</button>`}
        <button type="button" class="btn-danger-text" data-delete-address="${escapeHtml(address.id)}">Eliminar</button>
      </div>
    `;
    container.appendChild(item);
  });
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
    showMessage("Listo: esa es ahora tu dirección predeterminada.", "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo actualizar la dirección predeterminada.");
  }
}

async function deleteAddress(id) {
  const confirmed = await appConfirm("¿Eliminar esta dirección?");
  if (!confirmed) return;

  try {
    const response = await apiFetch(`${ADDRESSES_URL}${id}/`, { method: "DELETE" });
    if (response.status === 204) {
      await loadAddresses();
      showMessage("Dirección eliminada.", "success");
      return;
    }
    const data = await response.json().catch(() => ({}));
    throw new Error(getErrorMessage(data, "No se pudo eliminar la dirección."));
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo eliminar la dirección.");
  }
}

async function loadAddresses() {
  try {
    const response = await apiFetch(ADDRESSES_URL);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    addresses = extractResults(await response.json());
    renderAddressList();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar direcciones:", err);
    document.getElementById("addressList").innerHTML =
      '<p class="empty-state">No se pudieron cargar tus direcciones.</p>';
  }
}

document.addEventListener("click", (e) => {
  const setDefaultBtn = e.target.closest("[data-set-default]");
  if (setDefaultBtn) {
    setDefaultAddress(setDefaultBtn.dataset.setDefault);
    return;
  }
  const deleteBtn = e.target.closest("[data-delete-address]");
  if (deleteBtn) deleteAddress(deleteBtn.dataset.deleteAddress);
});

document.getElementById("toggleAddressFormBtn")?.addEventListener("click", () => {
  const form = document.getElementById("addressForm");
  form.style.display = form.style.display === "none" ? "block" : "none";
  if (form.style.display === "block") document.getElementById("addrLabel")?.focus();
});
document.getElementById("cancelAddressFormBtn")?.addEventListener("click", () => {
  resetAddressForm();
  document.getElementById("addressForm").style.display = "none";
});
document.getElementById("saveAddressBtn")?.addEventListener("click", saveNewAddress);

async function init() {
  try {
    const response = await apiFetch(ME_URL);
    if (response.ok) window.AppTopbar.render(await response.json());
  } catch (err) {
    if (err.isSessionExpired) return;
  }
  // Llegando desde "Agregar una dirección" del nuevo pedido, el formulario ya abierto.
  if (new URLSearchParams(location.search).has("nueva")) {
    document.getElementById("addressForm").style.display = "block";
  }
  await loadAddresses();
}

if (!window.Auth.hasSession()) {
  window.location.replace("index.html");
} else {
  init();
}
