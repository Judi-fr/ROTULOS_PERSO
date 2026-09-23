// Tarifas de envío (tarifas_envio.html): la tabla código postal -> precio con
// la que cotizamos el envío en el checkout de la tienda del comerciante.
//
// Es la pantalla que define lo que ve un comprador ajeno, así que todo lo que
// se guarda acá lo revalida el backend (ver apps.integrations.serializers
// .ShippingRateSerializer): los CP se normalizan, el rango se controla y la
// tienda se verifica contra el dueño de la sesión.
//
// Sesión y apiFetch salen de assets/js/auth.js (window.Auth); escapeHtml,
// showMessage y extractResults, de assets/js/utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const ME_URL = `${API_BASE}/auth/me/`;
const STORES_URL = `${API_BASE}/integrations/stores/`;
const RATES_URL = `${API_BASE}/integrations/shipping-rates/`;

const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

const storeSelect = document.getElementById("storeSelect");
const formCard = document.getElementById("formCard");
const listCard = document.getElementById("listCard");
const listEl = document.getElementById("rateList");
const form = document.getElementById("rateForm");
const formTitle = document.getElementById("formTitle");
const cancelBtn = document.getElementById("cancelBtn");
const saveBtn = document.getElementById("saveBtn");

const fields = {
  optionName: document.getElementById("optionName"),
  optionCode: document.getElementById("optionCode"),
  postalFrom: document.getElementById("postalFrom"),
  postalTo: document.getElementById("postalTo"),
  weightUpTo: document.getElementById("weightUpTo"),
  price: document.getElementById("price"),
  daysMin: document.getElementById("daysMin"),
  daysMax: document.getElementById("daysMax"),
};

let storeId = "";
// Id de la tarifa que se está editando, o null si el formulario agrega una.
let editingId = null;

// ---------------------------------------------------------------------------
// Tiendas
// ---------------------------------------------------------------------------
async function loadStores() {
  try {
    const response = await apiFetch(STORES_URL);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    extractResults(await response.json()).forEach((store) => {
      const option = document.createElement("option");
      option.value = store.id;
      option.textContent = store.name || `Tienda ${store.external_store_id}`;
      storeSelect.appendChild(option);
    });
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar las tiendas:", err);
    showMessage("No se pudieron cargar tus tiendas.");
  }
}

// ---------------------------------------------------------------------------
// Tabla
// ---------------------------------------------------------------------------
function weightLabel(rate) {
  return rate.weight_up_to_kg ? `hasta ${rate.weight_up_to_kg} kg` : "sin tope de peso";
}

function zoneLabel(rate) {
  return rate.postal_code_from === rate.postal_code_to
    ? `CP ${rate.postal_code_from}`
    : `CP ${rate.postal_code_from} a ${rate.postal_code_to}`;
}

function daysLabel(rate) {
  if (rate.delivery_days_min === null && rate.delivery_days_max === null) {
    return "sin plazo prometido";
  }
  if (rate.delivery_days_min === rate.delivery_days_max) {
    return `entrega en ${rate.delivery_days_min} día(s)`;
  }
  return `entrega en ${rate.delivery_days_min ?? "?"} a ${rate.delivery_days_max ?? "?"} día(s)`;
}

function buildItem(rate) {
  const item = document.createElement("div");
  item.className = `rate-item${rate.is_active ? "" : " inactive"}`;

  const info = document.createElement("div");
  info.className = "rate-item-info";
  info.innerHTML = `
    <p class="rate-item-title">${escapeHtml(rate.option_name)} · ${escapeHtml(zoneLabel(rate))}</p>
    <p class="rate-item-meta">${escapeHtml(weightLabel(rate))} · ${escapeHtml(daysLabel(rate))} · código ${escapeHtml(rate.option_code)}</p>
  `;

  const price = document.createElement("p");
  price.className = "rate-item-price";
  price.textContent = `$ ${rate.price}`;

  const actions = document.createElement("div");
  actions.className = "rate-item-actions";

  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "btn btn-outline btn-small";
  editBtn.textContent = "Editar";
  editBtn.addEventListener("click", () => startEdit(rate));

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.className = "btn-danger-text";
  deleteBtn.textContent = "Eliminar";
  deleteBtn.addEventListener("click", () => removeRate(rate));

  actions.append(editBtn, deleteBtn);
  item.append(info, price, actions);
  return item;
}

async function loadRates() {
  if (!storeId) return;
  try {
    const response = await apiFetch(`${RATES_URL}?store=${encodeURIComponent(storeId)}`);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    const rates = extractResults(await response.json());
    if (!rates.length) {
      listEl.innerHTML =
        '<p class="empty-state">Esta tienda todavía no tiene tarifas. Mientras no cargues ninguna, no vamos a cotizar envíos en su checkout.</p>';
      return;
    }
    listEl.innerHTML = "";
    rates.forEach((rate) => listEl.appendChild(buildItem(rate)));
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar las tarifas:", err);
    listEl.innerHTML = '<p class="empty-state">No se pudieron cargar las tarifas.</p>';
  }
}

// ---------------------------------------------------------------------------
// Alta, edición y baja
// ---------------------------------------------------------------------------
function resetForm() {
  editingId = null;
  form.reset();
  fields.optionCode.value = "standard";
  fields.optionName.value = "Envío estándar";
  formTitle.textContent = "Agregar una tarifa";
  saveBtn.textContent = "Guardar tarifa";
  cancelBtn.style.display = "none";
}

function startEdit(rate) {
  editingId = rate.id;
  fields.optionName.value = rate.option_name;
  fields.optionCode.value = rate.option_code;
  fields.postalFrom.value = rate.postal_code_from;
  fields.postalTo.value = rate.postal_code_to;
  fields.weightUpTo.value = rate.weight_up_to_kg ?? "";
  fields.price.value = rate.price;
  fields.daysMin.value = rate.delivery_days_min ?? "";
  fields.daysMax.value = rate.delivery_days_max ?? "";
  formTitle.textContent = "Editar tarifa";
  saveBtn.textContent = "Guardar cambios";
  cancelBtn.style.display = "inline-flex";
  formCard.scrollIntoView({ behavior: "smooth", block: "start" });
}

// Un campo numérico vacío va como null, no como "": el backend distingue
// "sin tope de peso" de "cero kilos".
function numberOrNull(input) {
  const value = input.value.trim();
  return value === "" ? null : Number(value);
}

function buildPayload() {
  return {
    connection: Number(storeId),
    option_name: fields.optionName.value.trim(),
    option_code: fields.optionCode.value.trim(),
    postal_code_from: fields.postalFrom.value.trim(),
    postal_code_to: fields.postalTo.value.trim(),
    weight_up_to_kg: numberOrNull(fields.weightUpTo),
    price: fields.price.value.trim(),
    delivery_days_min: numberOrNull(fields.daysMin),
    delivery_days_max: numberOrNull(fields.daysMax),
  };
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!storeId) {
    showMessage("Elegí una tienda primero.");
    return;
  }
  saveBtn.disabled = true;
  try {
    const response = await apiFetch(editingId ? `${RATES_URL}${editingId}/` : RATES_URL, {
      method: editingId ? "PATCH" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPayload()),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudo guardar la tarifa."));
    }
    showMessage(editingId ? "Tarifa actualizada." : "Tarifa agregada.", "success");
    resetForm();
    await loadRates();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al guardar la tarifa:", err);
    showMessage(err.message || "No se pudo guardar la tarifa.");
  } finally {
    saveBtn.disabled = false;
  }
});

cancelBtn.addEventListener("click", resetForm);

async function removeRate(rate) {
  const confirmed = window.confirm(
    `¿Eliminar la tarifa "${rate.option_name}" de ${zoneLabel(rate)}?\n\n` +
      "Si es la única que cubre esa zona, dejamos de cotizar envíos ahí."
  );
  if (!confirmed) return;
  try {
    const response = await apiFetch(`${RATES_URL}${rate.id}/`, { method: "DELETE" });
    if (!response.ok && response.status !== 204) {
      throw new Error("No se pudo eliminar la tarifa.");
    }
    showMessage("Tarifa eliminada.", "success");
    if (editingId === rate.id) resetForm();
    await loadRates();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al eliminar la tarifa:", err);
    showMessage(err.message || "No se pudo eliminar la tarifa.");
  }
}

storeSelect.addEventListener("change", (event) => {
  storeId = event.target.value;
  const chosen = Boolean(storeId);
  formCard.style.display = chosen ? "" : "none";
  listCard.style.display = chosen ? "" : "none";
  resetForm();
  if (chosen) loadRates();
});

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
    console.error("Error al cargar el perfil:", err);
  }
  await loadStores();
  resetForm();
}

init();
