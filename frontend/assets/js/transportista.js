// La cuenta del comerciante en un transportista (transportista.html?carrier=...).
// Backend: GET/PUT /api/v1/carriers/accounts/<carrier>/ (trae carrier_info: qué datos
// pide ese transportista), POST .../test/ y, si manda novedades,
// GET/POST .../push/ (apps/carriers/views.py y push_views.py). Las claves nunca
// vuelven del backend (has_<campo>): vacías en el formulario = no se cambian.
//
// Sesión y apiFetch salen de auth.js; showMessage y getErrorMessage, de utils.js.

const API_BASE = window.APP_CONFIG.API_BASE;
const CARRIER = new URLSearchParams(window.location.search).get("carrier") || "";
const ACCOUNT_URL = `${API_BASE}/carriers/accounts/${encodeURIComponent(CARRIER)}/`;
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

const SENDER_FIELDS = [
  "sender_name", "sender_email", "sender_phone", "sender_document",
  "origin_street", "origin_number", "origin_floor", "origin_apartment", "origin_postal_code", "origin_city", "origin_state",
];
const PACKAGE_FIELDS = ["default_weight_kg", "default_volume_cm3", "default_length_cm", "default_width_cm", "default_height_cm"];

let info = null;

function el(id) {
  return document.getElementById(id);
}

function make(tag, { className = "", text = "", attrs = {} } = {}) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
  return node;
}

// ---------------------------------------------------------------------------
// Lo que pide el transportista
// ---------------------------------------------------------------------------
function buildAccessFields() {
  const row = el("accessFields");
  row.querySelectorAll("[data-dynamic]").forEach((node) => node.remove());
  const hints = el("accessHints");
  hints.replaceChildren();
  info.fields.forEach((field) => {
    const wrapper = make("div", { className: "field", attrs: { "data-dynamic": "1" } });
    const id = `field_${field.name}`;
    const label = make("label", { text: field.label + (field.required_for === "quote" ? " (para cotizar)" : ""), attrs: { for: id } });
    let input;
    if (field.choices.length) {
      input = make("select", { attrs: { id } });
      input.appendChild(make("option", { text: "Elegí…", attrs: { value: "" } }));
      field.choices.forEach((choice) => input.appendChild(make("option", { text: choice.label, attrs: { value: choice.value } })));
    } else {
      input = make("input", {
        attrs: {
          id,
          type: field.secret ? "password" : "text",
          maxlength: String(field.max_length),
          autocomplete: field.secret ? "new-password" : "off",
          spellcheck: "false",
        },
      });
    }
    input.dataset.field = field.name;
    input.dataset.secret = field.secret ? "1" : "";
    wrapper.append(label, input);
    row.appendChild(wrapper);
    if (field.help) hints.appendChild(make("p", { className: "filters-hint", text: `${field.label}: ${field.help}` }));
  });
}

function addContractRow(contract = {}) {
  const row = make("div", { className: "filters-row contract-row" });

  const code = make("div", { className: "field" });
  code.appendChild(make("label", { text: info.contract_code_label }));
  const codeInput = make("input", { attrs: { type: "text", "data-role": "code", maxlength: "50" } });
  // Correo Argentino guarda "CP-D": el comerciante ve y escribe solo el servicio.
  codeInput.value = contract.service || contract.code || "";
  code.appendChild(codeInput);

  const label = make("div", { className: "field" });
  label.appendChild(make("label", { text: "Nombre (para reconocerlo)" }));
  const labelInput = make("input", { attrs: { type: "text", "data-role": "label", maxlength: "80" } });
  labelInput.value = contract.label || "";
  label.appendChild(labelInput);

  const kind = make("div", { className: "field" });
  kind.appendChild(make("label", { text: "Entrega" }));
  const select = make("select", { attrs: { "data-role": "kind" } });
  [["home", "A domicilio"], ["branch", `En ${info.branch_label}`]].forEach(([value, text]) => {
    select.appendChild(make("option", { text, attrs: { value } }));
  });
  select.value = contract.kind || "home";
  kind.appendChild(select);

  row.dataset.origin = contract.origin || "";
  const remove = make("button", { className: "btn-danger-text", text: "Quitar", attrs: { type: "button" } });
  remove.addEventListener("click", () => row.remove());
  row.append(code, label, kind, remove);
  if (contract.origin === "branch") row.appendChild(make("small", { className: "filters-hint", text: "Sale de una sucursal" }));
  el("contractsList").appendChild(row);
}

function collectContracts() {
  return Array.from(el("contractsList").querySelectorAll(".contract-row"))
    .map((row) => ({
      code: row.querySelector('[data-role="code"]').value.trim(),
      label: row.querySelector('[data-role="label"]').value.trim(),
      kind: row.querySelector('[data-role="kind"]').value,
      ...(row.dataset.origin ? { origin: row.dataset.origin } : {}),
    }))
    .filter((contract) => contract.code);
}

function setupPage() {
  document.title = `Cuenta de ${info.name}`;
  el("pageTitle").textContent = `Cuenta de ${info.name}`;
  el("accountHelp").textContent = `Se despacha con tu propia cuenta de ${info.name}. ${info.account_help}`;
  el("contractsTitle").textContent = `${info.contract_label}s`;
  el("contractsHelp").textContent = info.contract_help;
  el("addContractBtn").textContent = `+ Agregar ${info.contract_label.toLowerCase()}`;
  document.querySelectorAll('[data-package="volume"]').forEach((node) => {
    node.hidden = !info.uses_volume;
  });
  document.querySelectorAll('[data-package="dimensions"]').forEach((node) => {
    node.hidden = !info.uses_dimensions;
  });
  buildAccessFields();
  el("accountForm").hidden = false;
}

// ---------------------------------------------------------------------------
// Datos
// ---------------------------------------------------------------------------
function render(account) {
  el("environment").value = account.environment || "production";
  SENDER_FIELDS.concat(PACKAGE_FIELDS).forEach((name) => {
    el(name).value = account[name] ?? "";
  });
  document.querySelectorAll("[data-field]").forEach((input) => {
    const name = input.dataset.field;
    if (input.dataset.secret) {
      input.value = "";
      input.placeholder = account[`has_${name}`] ? "•••••••• (guardada)" : "";
    } else {
      input.value = account[name] || "";
    }
  });
  el("contractsList").replaceChildren();
  (account.contracts && account.contracts.length ? account.contracts : [{}]).forEach(addContractRow);

  const problems = [];
  if (account.last_error) problems.push(`${info.name} rechazó la última consulta: ${account.last_error}`);
  if (account.missing?.length) problems.push(`Para crear envíos falta: ${account.missing.join(", ")}.`);
  if (!account.missing?.length && account.quote_missing?.length) {
    problems.push(`Para ver el precio antes de despachar falta: ${account.quote_missing.join(", ")}.`);
  }
  el("missingNotice").textContent = problems.join(" ");
  el("missingNotice").style.display = problems.length ? "" : "none";
}

async function load() {
  try {
    const response = await apiFetch(ACCOUNT_URL);
    if (response.status === 404) {
      showMessage("Ese transportista no existe. Volvé a Mis pedidos y elegilo desde ahí.");
      return;
    }
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    const data = await response.json();
    info = data.carrier_info;
    setupPage();
    render(data);
    if (info.supports_push) loadPush(data.exists);
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage("No se pudo cargar tu cuenta.");
  }
}

async function save(event) {
  event.preventDefault();
  const payload = { environment: el("environment").value, contracts: collectContracts() };
  SENDER_FIELDS.forEach((name) => {
    payload[name] = el(name).value.trim();
  });
  PACKAGE_FIELDS.forEach((name) => {
    if (!el(name).closest("[hidden]")) payload[name] = el(name).value;
  });
  document.querySelectorAll("[data-field]").forEach((input) => {
    // Una clave vacía no se manda: queda la guardada.
    if (input.dataset.secret && !input.value) return;
    payload[input.dataset.field] = input.value.trim();
  });

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
    if (info.supports_push) loadPush(true);
    showMessage(
      data.missing?.length
        ? "Cuenta guardada. Todavía falta completar algunos datos para crear envíos (arriba)."
        : `Cuenta guardada. Probá la conexión para confirmar que ${info.name} acepta las credenciales.`,
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
    const response = await apiFetch(`${ACCOUNT_URL}test/`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    let message = data.detail || (response.ok ? "Conexión correcta." : `${info.name} no aceptó la conexión.`);
    // OCA trae sus operativas: se cargan si todavía no hay ninguna.
    if (response.ok && data.contracts?.length) {
      if (!collectContracts().length) {
        el("contractsList").replaceChildren();
        data.contracts.forEach(addContractRow);
        message += ` Se cargaron en la lista: quitá las que no uses y guardá.`;
      } else {
        message += ` Tu lista ya tiene ${info.contract_label.toLowerCase()}s: no se tocó.`;
      }
    }
    showMessage(message, response.ok ? "success" : "error");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage("No pudimos comunicarnos con el servidor.");
  } finally {
    button.disabled = false;
    button.textContent = "Probar conexión";
  }
}

// ---------------------------------------------------------------------------
// Novedades push
// ---------------------------------------------------------------------------
function renderPush(data) {
  el("pushHelp").textContent = info.push_help;
  const box = el("pushInfo");
  box.replaceChildren();
  if (!data.configured) {
    box.appendChild(make("p", { className: "filters-hint", text: "Todavía no generaste la dirección." }));
    el("pushBtn").textContent = "Generar dirección y credenciales";
    return;
  }
  const list = make("dl", { className: "push-credentials" });
  const add = (term, value) => list.append(make("dt", { text: term }), make("dd", { text: value }));
  add("Dirección (QA y producción)", data.url);
  add("Autenticación", "Basic");
  add("Usuario", data.username);
  add("Contraseña", data.password || "(se mostró una sola vez al generarla)");
  add(
    "Recibidas",
    data.received_count ? `${data.received_count}, la última el ${formatDate(data.last_received_at)}` : "Ninguna todavía"
  );
  box.appendChild(list);
  if (data.password) {
    box.appendChild(make("p", { className: "filters-hint", text: "Copiá la contraseña ahora: no se vuelve a mostrar." }));
  }
  el("pushBtn").textContent = "Generar credenciales nuevas";
}

async function loadPush(exists) {
  el("pushCard").hidden = false;
  el("pushBtn").disabled = !exists;
  if (!exists) {
    el("pushHelp").textContent = info.push_help;
    el("pushInfo").replaceChildren(make("p", { className: "filters-hint", text: "Guardá primero la cuenta." }));
    return;
  }
  try {
    const response = await apiFetch(`${ACCOUNT_URL}push/`);
    if (response.ok) renderPush(await response.json());
  } catch (err) {
    if (err.isSessionExpired) return;
  }
}

async function generatePush() {
  if (el("pushInfo").querySelector("dl") && !await appConfirm("Las credenciales anteriores dejan de funcionar. ¿Generar nuevas?")) return;
  const button = el("pushBtn");
  button.disabled = true;
  try {
    const response = await apiFetch(`${ACCOUNT_URL}push/`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron generar las credenciales."));
    renderPush(data);
    showMessage("Listo: pasale la dirección, el usuario y la contraseña a tu ejecutivo comercial.", "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudieron generar las credenciales.");
  } finally {
    button.disabled = false;
  }
}

el("accountForm").addEventListener("submit", save);
el("testBtn").addEventListener("click", testConnection);
el("addContractBtn").addEventListener("click", () => addContractRow());
el("pushBtn").addEventListener("click", generatePush);
document.getElementById("logoutBtn")?.addEventListener("click", () => window.Auth.logout());

if (!window.Auth.hasSession()) {
  window.location.replace("index.html");
} else {
  load();
  apiFetch(`${API_BASE}/auth/me/`)
    .then(async (response) => {
      if (response.ok) window.AppTopbar.render(await response.json());
    })
    .catch(() => {});
}
