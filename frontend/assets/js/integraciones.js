// Panel admin de integraciones (integraciones.html): ABM de claves de API
// (story 23) y de webhooks entrantes/salientes + su historial de entregas
// (story 24). Permiso único: integrations.manage (solo admin, ver
// permissions_map.py). Sesión y apiFetch salen de assets/js/auth.js
// (window.Auth), igual que el resto del frontend.

const API_BASE = window.APP_CONFIG.API_BASE;
const ME_URL = `${API_BASE}/auth/me/`;
const USERS_SEARCH_URL = `${API_BASE}/users/`;
const KEYS_URL = `${API_BASE}/integrations/keys/`;
const INCOMING_URL = `${API_BASE}/integrations/incoming-webhooks/`;
const OUTGOING_URL = `${API_BASE}/integrations/webhook-endpoints/`;
const DELIVERIES_URL = `${API_BASE}/integrations/webhook-deliveries/`;

const apiFetch = (url, options) => window.Auth.apiFetch(url, options);
const getAccessToken = () => window.Auth.getAccessToken();
const getCurrentUser = () => window.Auth.getCurrentUser();

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function showMessage(text, type = "error") {
  const el = document.getElementById("pageMessage");
  if (!el) return;
  el.textContent = text;
  el.className = `page-message ${type}`;
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
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function renderTopbar(user) {
  const nameEl = document.getElementById("userName");
  const emailEl = document.getElementById("userEmail");
  const avatarEl = document.getElementById("userAvatar");
  const email = user.email || "";
  const name = [user.first_name, user.last_name].filter(Boolean).join(" ") || email || "Usuario";

  if (nameEl) nameEl.textContent = name;
  if (emailEl) emailEl.textContent = email || "—";
  if (avatarEl) {
    const picture = getCurrentUser().picture;
    avatarEl.src = picture
      ? picture
      : `https://api.dicebear.com/7.x/avataaars/svg?seed=${encodeURIComponent(email || "user")}`;
  }
}

document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

// ---------------------------------------------------------------------------
// TABS
// ---------------------------------------------------------------------------
const TABS = ["Keys", "Incoming", "Outgoing", "Deliveries"];
function activateTab(name) {
  TABS.forEach((tab) => {
    document.getElementById(`tab${tab}Btn`)?.classList.toggle("active", tab === name);
    document.getElementById(`tab${tab}Panel`)?.classList.toggle("active", tab === name);
  });
}
TABS.forEach((tab) => {
  document.getElementById(`tab${tab}Btn`)?.addEventListener("click", () => activateTab(tab));
});

// ---------------------------------------------------------------------------
// PICKER DE USUARIO DUEÑO: reutilizado por los tres formularios (claves,
// webhooks entrantes, webhooks salientes). Cada instancia guarda su propio
// id/email elegido -- no hay estado global compartido entre formularios.
// ---------------------------------------------------------------------------
function createOwnerPicker(prefix) {
  const searchInput = document.getElementById(`${prefix}OwnerSearch`);
  const searchBtn = document.getElementById(`${prefix}OwnerSearchBtn`);
  const resultsEl = document.getElementById(`${prefix}OwnerResults`);
  const selectedEl = document.getElementById(`${prefix}OwnerSelected`);
  let selected = null;

  function renderSelected() {
    selectedEl.textContent = selected ? `Dueño elegido: ${selected.email} (ID ${selected.id})` : "";
  }

  async function search() {
    const query = searchInput.value.trim();
    if (!query) return;
    resultsEl.innerHTML = '<div class="owner-result-item">Buscando...</div>';
    try {
      const response = await apiFetch(`${USERS_SEARCH_URL}?search=${encodeURIComponent(query)}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo buscar usuarios."));
      const users = extractResults(data);
      if (!users.length) {
        resultsEl.innerHTML = '<div class="owner-result-item">Sin resultados.</div>';
        return;
      }
      resultsEl.innerHTML = "";
      users.forEach((u) => {
        const item = document.createElement("div");
        item.className = "owner-result-item";
        item.textContent = `${u.email} (ID ${u.id})`;
        item.addEventListener("click", () => {
          selected = { id: u.id, email: u.email };
          renderSelected();
          resultsEl.innerHTML = "";
          searchInput.value = "";
        });
        resultsEl.appendChild(item);
      });
    } catch (err) {
      if (err.isSessionExpired) return;
      resultsEl.innerHTML = `<div class="owner-result-item">${escapeHtml(err.message)}</div>`;
    }
  }

  searchBtn?.addEventListener("click", search);
  searchInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      search();
    }
  });

  return {
    getOwnerId: () => (selected ? selected.id : null),
    reset: () => {
      selected = null;
      renderSelected();
      resultsEl.innerHTML = "";
    },
  };
}

const keyOwnerPicker = createOwnerPicker("key");
const incomingOwnerPicker = createOwnerPicker("incoming");
const outgoingOwnerPicker = createOwnerPicker("outgoing");

// ---------------------------------------------------------------------------
// CLAVES DE API (story 23)
// ---------------------------------------------------------------------------
function showFormMsg(elId, text, ok) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.textContent = text;
  el.className = `page-message ${ok ? "success" : "error"}`;
  el.style.display = "block";
}

function renderKeysTable(keys) {
  const body = document.getElementById("keysTableBody");
  if (!body) return;
  if (!keys.length) {
    body.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:20px;">Sin claves creadas todavía.</td></tr>';
    return;
  }
  body.innerHTML = keys
    .map(
      (k) => `
      <tr data-id="${k.id}">
        <td>${escapeHtml(k.name)}</td>
        <td class="mono">${escapeHtml(k.prefix)}…</td>
        <td>${escapeHtml(k.owner_email || "-")}</td>
        <td><span class="status-badge ${k.is_active ? "active" : "inactive"}">${k.is_active ? "Activa" : "Revocada"}</span></td>
        <td>${formatDate(k.last_used_at)}</td>
        <td>${formatDate(k.created_at)}</td>
        <td>
          <button class="btn-danger-text" data-action="toggle">${k.is_active ? "Revocar" : "Reactivar"}</button>
          <button class="btn-danger-text" data-action="delete">Eliminar</button>
        </td>
      </tr>`
    )
    .join("");
}

let allKeys = [];
async function loadKeys() {
  try {
    const response = await apiFetch(KEYS_URL);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar las claves."));
    allKeys = extractResults(data);
    renderKeysTable(allKeys);
    populateIncomingKeySelect();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar claves:", err);
    document.getElementById("keysTableBody").innerHTML =
      `<tr><td colspan="7" style="text-align:center; padding:20px; color:red;">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById("keyForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const ownerId = keyOwnerPicker.getOwnerId();
  const name = document.getElementById("keyName").value.trim();
  if (!ownerId) {
    showFormMsg("keyFormMsg", "Buscá y elegí el usuario dueño de la clave.", false);
    return;
  }
  try {
    const response = await apiFetch(KEYS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, owner: ownerId }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo crear la clave."));

    const banner = document.getElementById("newKeyBanner");
    document.getElementById("newKeyValue").textContent = data.key;
    banner.classList.add("show");

    event.target.reset();
    keyOwnerPicker.reset();
    showFormMsg("keyFormMsg", "Clave creada.", true);
    await loadKeys();
  } catch (err) {
    if (err.isSessionExpired) return;
    showFormMsg("keyFormMsg", err.message || "No se pudo crear la clave.", false);
  }
});

document.getElementById("copyNewKeyBtn")?.addEventListener("click", async () => {
  const text = document.getElementById("newKeyValue").textContent;
  try {
    await navigator.clipboard.writeText(text);
    showMessage("Clave copiada al portapapeles.", "success");
  } catch {
    // Sin permiso de portapapeles: la clave sigue visible en pantalla para copiarla a mano.
  }
});

document.getElementById("keysTableBody")?.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const row = event.target.closest("tr[data-id]");
  const id = row.dataset.id;
  const key = allKeys.find((k) => String(k.id) === String(id));
  if (!key) return;

  if (button.dataset.action === "toggle") {
    try {
      const response = await apiFetch(`${KEYS_URL}${id}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: !key.is_active }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo actualizar la clave."));
      await loadKeys();
    } catch (err) {
      if (err.isSessionExpired) return;
      showMessage(err.message || "No se pudo actualizar la clave.");
    }
  } else if (button.dataset.action === "delete") {
    if (!window.confirm(`¿Eliminar la clave "${key.name}"? Esto no se puede deshacer.`)) return;
    try {
      const response = await apiFetch(`${KEYS_URL}${id}/`, { method: "DELETE" });
      if (!response.ok && response.status !== 204) {
        const data = await response.json().catch(() => ({}));
        throw new Error(getErrorMessage(data, "No se pudo eliminar la clave."));
      }
      await loadKeys();
    } catch (err) {
      if (err.isSessionExpired) return;
      showMessage(err.message || "No se pudo eliminar la clave.");
    }
  }
});

// ---------------------------------------------------------------------------
// WEBHOOKS ENTRANTES (story 24)
// ---------------------------------------------------------------------------
function populateIncomingKeySelect() {
  const select = document.getElementById("incomingKeySelect");
  if (!select) return;
  const current = select.value;
  select.innerHTML = '<option value="">Elegí una clave...</option>';
  allKeys
    .filter((k) => k.is_active)
    .forEach((k) => {
      const opt = document.createElement("option");
      opt.value = k.id;
      opt.textContent = `${k.name} (${k.owner_email})`;
      select.appendChild(opt);
    });
  if (current) select.value = current;
}

function renderIncomingTable(webhooks) {
  const body = document.getElementById("incomingTableBody");
  if (!body) return;
  if (!webhooks.length) {
    body.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:20px;">Sin webhooks entrantes todavía.</td></tr>';
    return;
  }
  body.innerHTML = webhooks
    .map(
      (w) => `
      <tr data-id="${w.id}">
        <td>${escapeHtml(w.name)}</td>
        <td class="mono">${escapeHtml(w.webhook_url)}</td>
        <td class="mono">${escapeHtml(w.secret)}</td>
        <td>${escapeHtml(w.integration_key_name || "-")}</td>
        <td><span class="status-badge ${w.is_active ? "active" : "inactive"}">${w.is_active ? "Activo" : "Inactivo"}</span></td>
        <td>
          <button class="btn-danger-text" data-action="toggle">${w.is_active ? "Desactivar" : "Activar"}</button>
          <button class="btn-danger-text" data-action="delete">Eliminar</button>
        </td>
      </tr>`
    )
    .join("");
}

let allIncoming = [];
async function loadIncoming() {
  try {
    const response = await apiFetch(INCOMING_URL);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar los webhooks entrantes."));
    allIncoming = extractResults(data);
    renderIncomingTable(allIncoming);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar webhooks entrantes:", err);
    document.getElementById("incomingTableBody").innerHTML =
      `<tr><td colspan="6" style="text-align:center; padding:20px; color:red;">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById("incomingForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const ownerId = incomingOwnerPicker.getOwnerId();
  if (!ownerId) {
    showFormMsg("incomingFormMsg", "Buscá y elegí el usuario dueño del webhook.", false);
    return;
  }
  const integrationKey = document.getElementById("incomingKeySelect").value;
  if (!integrationKey) {
    showFormMsg("incomingFormMsg", "Elegí la clave de API asociada.", false);
    return;
  }
  const mappingRaw = document.getElementById("incomingMapping").value.trim();
  let mapping = {};
  if (mappingRaw) {
    try {
      mapping = JSON.parse(mappingRaw);
    } catch {
      showFormMsg("incomingFormMsg", "El mapeo tiene que ser un JSON válido.", false);
      return;
    }
  }

  try {
    const response = await apiFetch(INCOMING_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: document.getElementById("incomingName").value.trim(),
        slug: document.getElementById("incomingSlug").value.trim(),
        owner: ownerId,
        integration_key: integrationKey,
        mapping,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo crear el webhook entrante."));

    event.target.reset();
    incomingOwnerPicker.reset();
    showFormMsg("incomingFormMsg", "Webhook entrante creado.", true);
    await loadIncoming();
  } catch (err) {
    if (err.isSessionExpired) return;
    showFormMsg("incomingFormMsg", err.message || "No se pudo crear el webhook entrante.", false);
  }
});

document.getElementById("incomingTableBody")?.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const row = event.target.closest("tr[data-id]");
  const id = row.dataset.id;
  const webhook = allIncoming.find((w) => String(w.id) === String(id));
  if (!webhook) return;

  if (button.dataset.action === "toggle") {
    try {
      const response = await apiFetch(`${INCOMING_URL}${id}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: !webhook.is_active }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo actualizar el webhook."));
      await loadIncoming();
    } catch (err) {
      if (err.isSessionExpired) return;
      showMessage(err.message || "No se pudo actualizar el webhook.");
    }
  } else if (button.dataset.action === "delete") {
    if (!window.confirm(`¿Eliminar el webhook "${webhook.name}"?`)) return;
    try {
      const response = await apiFetch(`${INCOMING_URL}${id}/`, { method: "DELETE" });
      if (!response.ok && response.status !== 204) {
        const data = await response.json().catch(() => ({}));
        throw new Error(getErrorMessage(data, "No se pudo eliminar el webhook."));
      }
      await loadIncoming();
    } catch (err) {
      if (err.isSessionExpired) return;
      showMessage(err.message || "No se pudo eliminar el webhook.");
    }
  }
});

// ---------------------------------------------------------------------------
// WEBHOOKS SALIENTES (story 24)
// ---------------------------------------------------------------------------
function renderOutgoingTable(endpoints) {
  const body = document.getElementById("outgoingTableBody");
  if (!body) return;
  if (!endpoints.length) {
    body.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:20px;">Sin webhooks salientes todavía.</td></tr>';
    return;
  }
  body.innerHTML = endpoints
    .map(
      (w) => `
      <tr data-id="${w.id}">
        <td>${escapeHtml(w.name || "-")}</td>
        <td class="mono">${escapeHtml(w.url)}</td>
        <td class="mono">${escapeHtml(w.secret)}</td>
        <td>${w.events && w.events.length ? escapeHtml(w.events.join(", ")) : "Todos"}</td>
        <td><span class="status-badge ${w.is_active ? "active" : "inactive"}">${w.is_active ? "Activo" : "Inactivo"}</span></td>
        <td>
          <button class="btn-danger-text" data-action="toggle">${w.is_active ? "Desactivar" : "Activar"}</button>
          <button class="btn-danger-text" data-action="delete">Eliminar</button>
        </td>
      </tr>`
    )
    .join("");
}

let allOutgoing = [];
async function loadOutgoing() {
  try {
    const response = await apiFetch(OUTGOING_URL);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar los webhooks salientes."));
    allOutgoing = extractResults(data);
    renderOutgoingTable(allOutgoing);
    populateDeliveriesFilter();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar webhooks salientes:", err);
    document.getElementById("outgoingTableBody").innerHTML =
      `<tr><td colspan="6" style="text-align:center; padding:20px; color:red;">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById("outgoingForm")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const ownerId = outgoingOwnerPicker.getOwnerId();
  if (!ownerId) {
    showFormMsg("outgoingFormMsg", "Buscá y elegí el usuario dueño del webhook.", false);
    return;
  }
  const eventsRaw = document.getElementById("outgoingEvents").value.trim();
  const events = eventsRaw ? eventsRaw.split(",").map((e) => e.trim()).filter(Boolean) : [];

  try {
    const response = await apiFetch(OUTGOING_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: document.getElementById("outgoingName").value.trim(),
        url: document.getElementById("outgoingUrl").value.trim(),
        owner: ownerId,
        events,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo crear el webhook saliente."));

    event.target.reset();
    outgoingOwnerPicker.reset();
    showFormMsg("outgoingFormMsg", "Webhook saliente creado.", true);
    await loadOutgoing();
  } catch (err) {
    if (err.isSessionExpired) return;
    showFormMsg("outgoingFormMsg", err.message || "No se pudo crear el webhook saliente.", false);
  }
});

document.getElementById("outgoingTableBody")?.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const row = event.target.closest("tr[data-id]");
  const id = row.dataset.id;
  const endpoint = allOutgoing.find((w) => String(w.id) === String(id));
  if (!endpoint) return;

  if (button.dataset.action === "toggle") {
    try {
      const response = await apiFetch(`${OUTGOING_URL}${id}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: !endpoint.is_active }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo actualizar el webhook."));
      await loadOutgoing();
    } catch (err) {
      if (err.isSessionExpired) return;
      showMessage(err.message || "No se pudo actualizar el webhook.");
    }
  } else if (button.dataset.action === "delete") {
    if (!window.confirm(`¿Eliminar el webhook "${endpoint.name || endpoint.url}"?`)) return;
    try {
      const response = await apiFetch(`${OUTGOING_URL}${id}/`, { method: "DELETE" });
      if (!response.ok && response.status !== 204) {
        const data = await response.json().catch(() => ({}));
        throw new Error(getErrorMessage(data, "No se pudo eliminar el webhook."));
      }
      await loadOutgoing();
    } catch (err) {
      if (err.isSessionExpired) return;
      showMessage(err.message || "No se pudo eliminar el webhook.");
    }
  }
});

// ---------------------------------------------------------------------------
// HISTORIAL DE ENTREGAS (solo lectura)
// ---------------------------------------------------------------------------
function populateDeliveriesFilter() {
  const select = document.getElementById("deliveriesEndpointFilter");
  if (!select) return;
  const current = select.value;
  select.innerHTML = '<option value="">Todos los webhooks salientes</option>';
  allOutgoing.forEach((w) => {
    const opt = document.createElement("option");
    opt.value = w.id;
    opt.textContent = w.name || w.url;
    select.appendChild(opt);
  });
  if (current) select.value = current;
}

function renderDeliveriesTable(deliveries) {
  const body = document.getElementById("deliveriesTableBody");
  if (!body) return;
  if (!deliveries.length) {
    body.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:20px;">Sin entregas registradas todavía.</td></tr>';
    return;
  }
  body.innerHTML = deliveries
    .map(
      (d) => `
      <tr>
        <td>${escapeHtml(d.event)}</td>
        <td>${d.status_code ?? "-"}</td>
        <td><span class="status-badge ${d.success ? "active" : "inactive"}">${d.success ? "OK" : "Falló"}</span></td>
        <td class="mono" title="${escapeHtml(d.response_body || "")}">${escapeHtml((d.response_body || "").slice(0, 60))}</td>
        <td>${formatDate(d.created_at)}</td>
      </tr>`
    )
    .join("");
}

async function loadDeliveries() {
  const endpointId = document.getElementById("deliveriesEndpointFilter")?.value || "";
  const url = endpointId ? `${DELIVERIES_URL}?endpoint=${encodeURIComponent(endpointId)}` : DELIVERIES_URL;
  try {
    const response = await apiFetch(url);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo cargar el historial."));
    renderDeliveriesTable(extractResults(data));
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar el historial de entregas:", err);
    document.getElementById("deliveriesTableBody").innerHTML =
      `<tr><td colspan="5" style="text-align:center; padding:20px; color:red;">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById("deliveriesEndpointFilter")?.addEventListener("change", loadDeliveries);
document.getElementById("tabDeliveriesBtn")?.addEventListener("click", loadDeliveries);

// ---------------------------------------------------------------------------
// INICIO: pantalla exclusiva de integrations.manage (solo admin).
// ---------------------------------------------------------------------------
async function init() {
  try {
    const response = await apiFetch(ME_URL);
    if (response.ok) {
      const user = await response.json();
      renderTopbar(user);
      const permissions = Array.isArray(user.permissions) ? user.permissions : [];
      if (!permissions.includes("integrations.manage")) {
        window.location.replace("dashboard.html");
        return;
      }
    }
  } catch (err) {
    if (err.isSessionExpired) return;
  }

  await loadKeys();
  await loadIncoming();
  await loadOutgoing();
}

if (!getAccessToken()) {
  window.location.replace("index.html");
} else {
  init();
}
