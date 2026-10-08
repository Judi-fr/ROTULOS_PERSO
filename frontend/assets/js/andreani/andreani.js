// Cuenta de Andreani del cliente (andreani.html). Backend: GET/PUT
// /api/v1/carriers/andreani/account/ y POST .../account/test/
// (apps/carriers/andreani/). La contraseña nunca vuelve del backend: vacía en
// el formulario = no se cambia.
//
// Sesión y apiFetch salen de auth.js; showMessage y getErrorMessage, de utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const ACCOUNT_URL = `${API_BASE}/carriers/andreani/account/`;
const TEST_URL = `${API_BASE}/carriers/andreani/account/test/`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

const TEXT_FIELDS = [
  "username", "client_code", "sender_name", "sender_email", "sender_phone", "sender_document",
  "origin_street", "origin_number", "origin_floor", "origin_apartment", "origin_postal_code", "origin_city",
];
const KIND_LABELS = { home: "Entrega a domicilio", branch: "Entrega en sucursal / punto HOP" };

function el(id) {
  return document.getElementById(id);
}

function addContractRow(contract = {}) {
  const row = document.createElement("div");
  row.className = "filters-row contract-row";

  const code = document.createElement("div");
  code.className = "field";
  code.innerHTML = '<label>Número de contrato</label><input type="text" data-role="code" maxlength="50" />';
  code.querySelector("input").value = contract.code || "";

  const label = document.createElement("div");
  label.className = "field";
  label.innerHTML = '<label>Nombre (para reconocerlo)</label><input type="text" data-role="label" maxlength="80" />';
  label.querySelector("input").value = contract.label || "";

  const kind = document.createElement("div");
  kind.className = "field";
  kind.innerHTML = "<label>Servicio</label>";
  const select = document.createElement("select");
  select.dataset.role = "kind";
  Object.entries(KIND_LABELS).forEach(([value, text]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    select.appendChild(option);
  });
  select.value = contract.kind || "home";
  kind.appendChild(select);

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "btn-danger-text";
  remove.textContent = "Quitar";
  remove.addEventListener("click", () => row.remove());

  row.append(code, label, kind, remove);
  el("contractsList").appendChild(row);
}

function collectContracts() {
  return Array.from(el("contractsList").querySelectorAll(".contract-row"))
    .map((row) => ({
      code: row.querySelector('[data-role="code"]').value.trim(),
      label: row.querySelector('[data-role="label"]').value.trim(),
      kind: row.querySelector('[data-role="kind"]').value,
    }))
    .filter((contract) => contract.code);
}

function render(account) {
  TEXT_FIELDS.forEach((field) => {
    el(field).value = account[field] || "";
  });
  el("environment").value = account.environment || "production";
  el("default_weight_kg").value = account.default_weight_kg || "1";
  el("default_volume_cm3").value = account.default_volume_cm3 || "4000";
  el("password").value = "";
  el("password").placeholder = account.has_password ? "•••••••• (guardada)" : "";
  el("contractsList").innerHTML = "";
  (account.contracts && account.contracts.length ? account.contracts : [{}]).forEach(addContractRow);

  const notice = el("missingNotice");
  const problems = [];
  if (account.last_error) problems.push(`Andreani rechazó la última consulta: ${account.last_error}`);
  if (account.missing && account.missing.length) {
    problems.push(`Para crear envíos falta: ${account.missing.join(", ")}.`);
  }
  notice.textContent = problems.join(" ");
  notice.style.display = problems.length ? "" : "none";
}

async function load() {
  try {
    const response = await apiFetch(ACCOUNT_URL);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    render(await response.json());
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage("No se pudo cargar tu cuenta de Andreani.");
  }
}

async function save(event) {
  event.preventDefault();
  const payload = {
    environment: el("environment").value,
    contracts: collectContracts(),
    default_weight_kg: el("default_weight_kg").value,
    default_volume_cm3: el("default_volume_cm3").value,
  };
  TEXT_FIELDS.forEach((field) => {
    payload[field] = el(field).value.trim();
  });
  if (el("password").value) payload.password = el("password").value;

  const button = el("saveBtn");
  button.disabled = true;
  try {
    const response = await apiFetch(ACCOUNT_URL, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo guardar la cuenta."));
    render(data);
    showMessage(
      data.missing && data.missing.length
        ? "Cuenta guardada. Todavía falta completar algunos datos para crear envíos (arriba)."
        : "Cuenta guardada. Probá la conexión para confirmar que Andreani acepta las credenciales.",
      "success"
    );
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo guardar la cuenta.");
  } finally {
    button.disabled = false;
  }
}

async function testConnection() {
  const button = el("testBtn");
  button.disabled = true;
  button.textContent = "Probando...";
  try {
    const response = await apiFetch(TEST_URL, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    showMessage(data.detail || (response.ok ? "Conexión correcta." : "Andreani no aceptó la conexión."), response.ok ? "success" : "error");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage("No pudimos comunicarnos con el servidor.");
  } finally {
    button.disabled = false;
    button.textContent = "Probar conexión";
  }
}

el("accountForm").addEventListener("submit", save);
el("testBtn").addEventListener("click", testConnection);
el("addContractBtn").addEventListener("click", () => addContractRow());
document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

if (!window.Auth.getAccessToken()) {
  window.location.replace("index.html");
} else {
  load();
  apiFetch(`${API_BASE}/auth/me/`)
    .then(async (response) => {
      if (response.ok) window.AppTopbar.render(await response.json());
    })
    .catch(() => {});
}
