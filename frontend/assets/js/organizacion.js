// Datos de la cuenta (organizacion.html, GET/PATCH /auth/me/account/): los
// datos fiscales, el remitente y logo por defecto de los rótulos y las
// preferencias. El backend valida (CUIT, que la plantilla y el transportista
// existan); acá se arma el formulario y se manda.
//
// Comparte la página con perfil.js ("Mi cuenta"): todo va en su propio ámbito
// para que sus constantes (API_BASE, apiFetch) no choquen con las de aquel.
(function () {
  const API_BASE = window.APP_CONFIG.API_BASE;
  const ACCOUNT_URL = `${API_BASE}/auth/me/account/`;
  const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

  // El logo solo viaja si se cambió: un data URL nuevo, o "" para quitarlo.
  let logoChange = null;

  const FIELDS = {
    legal_name: "legalName",
    tax_id: "taxId",
    sender_name: "senderName",
    sender_address: "senderAddress",
    sender_phone: "senderPhone",
    default_template: "defaultTemplate",
    default_carrier: "defaultCarrier",
    default_print_format: "defaultPrintFormat",
  };

  async function readAll(url) {
    // Las listas paginadas (plantillas): se siguen los "next" hasta el final.
    const items = [];
    let next = url;
    while (next) {
      const response = await apiFetch(next);
      if (!response.ok) break;
      const data = await response.json();
      items.push(...extractResults(data));
      next = Array.isArray(data) ? null : data.next;
    }
    return items;
  }

  async function fillOptions() {
    const [templates, carriers, manualCarriers] = await Promise.all([
      readAll(`${API_BASE}/labels/templates/`),
      readAll(`${API_BASE}/carriers/`),
      readAll(`${API_BASE}/carriers/manual-carriers/`),
    ]);
    document.getElementById("defaultTemplate").insertAdjacentHTML(
      "beforeend",
      templates
        .map((template) => `<option value="${template.id}">${escapeHtml(template.name)}</option>`)
        .join("")
    );
    const withApi = carriers.map((carrier) => `<option value="${escapeHtml(carrier.code)}">${escapeHtml(carrier.name)}</option>`);
    const manual = manualCarriers.map((carrier) => `<option value="${escapeHtml(carrier.name)}">${escapeHtml(carrier.name)}</option>`);
    const carrierSelect = document.getElementById("defaultCarrier");
    if (withApi.length) carrierSelect.insertAdjacentHTML("beforeend", `<optgroup label="Con cuenta conectada">${withApi.join("")}</optgroup>`);
    if (manual.length) carrierSelect.insertAdjacentHTML("beforeend", `<optgroup label="Cargando el seguimiento a mano">${manual.join("")}</optgroup>`);
  }

  function showLogo(url) {
    const preview = document.getElementById("logoPreview");
    preview.hidden = !url;
    if (url) preview.src = url;
    document.getElementById("logoRemove").hidden = !url;
  }

  function fillForm(data) {
    Object.entries(FIELDS).forEach(([key, id]) => {
      const value = data[key];
      document.getElementById(id).value = value === null || value === undefined ? "" : String(value);
    });
    showLogo(data.logo || "");
    logoChange = null;
  }

  function readFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error("No se pudo leer la imagen."));
      reader.readAsDataURL(file);
    });
  }

  async function save(event) {
    event.preventDefault();
    const payload = {};
    Object.entries(FIELDS).forEach(([key, id]) => {
      payload[key] = document.getElementById(id).value.trim();
    });
    payload.default_template = payload.default_template ? Number(payload.default_template) : null;
    if (logoChange !== null) payload.logo = logoChange;

    const button = document.getElementById("saveBtn");
    button.disabled = true;
    try {
      const response = await apiFetch(ACCOUNT_URL, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron guardar los datos."));
      fillForm(data);
      showMessage("Datos de la cuenta guardados.", "success");
    } catch (err) {
      if (err.isSessionExpired) return;
      showMessage(err.message);
    } finally {
      button.disabled = false;
    }
  }

  // Sin el acceso "Datos del comercio" (account.manage) se ven pero no se
  // cambian: el backend igual rechazaría el guardado.
  function makeReadOnly() {
    const form = document.getElementById("accountForm");
    form.querySelectorAll("input, select, textarea, button").forEach((el) => {
      el.disabled = true;
    });
    form.querySelector(".account-actions").hidden = true;
    document.getElementById("logoInput").hidden = true;
    document.getElementById("logoRemove").hidden = true;
    const note = document.createElement("p");
    note.className = "account-section-help";
    note.textContent = "Solo los podés ver: cambiarlos necesita el acceso «Datos del comercio».";
    form.before(note);
  }

  async function init(canEdit) {
    window.AppTopbar.render();
    document.getElementById("accountForm").addEventListener("submit", save);
    document.getElementById("logoInput").addEventListener("change", async (event) => {
      const file = event.target.files[0];
      if (!file) return;
      try {
        logoChange = await readFile(file);
        showLogo(logoChange);
      } catch (err) {
        showMessage(err.message);
      }
    });
    document.getElementById("logoRemove").addEventListener("click", () => {
      logoChange = "";
      document.getElementById("logoInput").value = "";
      showLogo("");
    });
    try {
      await fillOptions();
      const response = await apiFetch(ACCOUNT_URL);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron leer los datos de la cuenta."));
      fillForm(data);
      if (!canEdit) makeReadOnly();
    } catch (err) {
      if (err.isSessionExpired) return;
      showMessage(err.message);
    }
  }

  // Sin comercio no hay datos del comercio: la sección se reemplaza por el
  // camino para crear uno o sumarse (el perfil personal sigue arriba).
  function hideBusinessSection() {
    const title = document.getElementById("comercio");
    const help = title.nextElementSibling;
    document.getElementById("accountForm").hidden = true;
    if (help) help.hidden = true;
    const note = document.createElement("p");
    note.className = "account-section-help";
    note.textContent = "Todavía no estás en ningún comercio. ";
    const link = document.createElement("a");
    link.href = "comercio_inicio.html";
    link.textContent = "Crear uno o sumarte a uno";
    note.appendChild(link);
    title.after(note);
  }

  if (!window.Auth.hasSession()) {
    window.location.replace("index.html");
  } else if (!window.Auth.getCurrentUser().team) {
    hideBusinessSection();
  } else {
    const team = window.Auth.getCurrentUser().team;
    document.getElementById("comercio").textContent = `Datos del comercio «${team.display_name || team.name}»`;
    init(sessionHasPermission("account.manage"));
  }
})();
