// Catálogos globales (catalogos.html, admin con catalogs.manage):
// transportistas sin API (/carriers/manual-carriers/) y tamaños de rótulo
// (/labels/sizes/). Las dos tablas funcionan igual: agregar, editar,
// desactivar y volver a activar (borrar desactiva, ver apps/common/catalogs.py).

// apiFetch viene de admin_common.js, como en las demás páginas de admin.
const API_BASE = window.APP_CONFIG.API_BASE;

const CATALOGS = {
  carriers: {
    url: `${API_BASE}/carriers/manual-carriers/`,
    body: "carriersBody",
    form: "carrierForm",
    idInput: "carrierId",
    submit: "carrierSubmit",
    cancel: "carrierCancel",
    noun: "Transportista",
    columns: (item) => [
      `<strong>${escapeHtml(item.name)}</strong>`,
      item.tracking_url_template
        ? `<span class="catalog-muted">${escapeHtml(item.tracking_url_template)}</span>`
        : '<span class="catalog-muted">Sin link</span>',
    ],
    fill: (item) => {
      document.getElementById("carrierName").value = item.name;
      document.getElementById("carrierUrl").value = item.tracking_url_template || "";
    },
    read: () => ({
      name: document.getElementById("carrierName").value.trim(),
      tracking_url_template: document.getElementById("carrierUrl").value.trim(),
    }),
  },
  sizes: {
    url: `${API_BASE}/labels/sizes/`,
    body: "sizesBody",
    form: "sizeForm",
    idInput: "sizeId",
    submit: "sizeSubmit",
    cancel: "sizeCancel",
    noun: "Tamaño",
    columns: (item) => [
      `<strong>${escapeHtml(item.name)}</strong>`,
      `${Number(item.width_cm)} × ${Number(item.height_cm)} cm`,
    ],
    fill: (item) => {
      document.getElementById("sizeName").value = item.name;
      document.getElementById("sizeWidth").value = Number(item.width_cm);
      document.getElementById("sizeHeight").value = Number(item.height_cm);
    },
    read: () => ({
      name: document.getElementById("sizeName").value.trim(),
      width_cm: document.getElementById("sizeWidth").value,
      height_cm: document.getElementById("sizeHeight").value,
    }),
  },
};

async function send(url, method, payload, fallback) {
  const response = await apiFetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: payload ? JSON.stringify(payload) : undefined,
  });
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(getErrorMessage(data, fallback));
  return data;
}

function rowButton(text, kind, onClick) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `btn ${kind}`;
  button.textContent = text;
  button.addEventListener("click", onClick);
  return button;
}

async function load(catalog) {
  const body = document.getElementById(catalog.body);
  try {
    const response = await apiFetch(`${catalog.url}?include_inactive=1`);
    const data = await response.json().catch(() => []);
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo leer el catálogo."));
    const items = extractResults(data);
    body.innerHTML = items.length ? "" : '<tr><td colspan="4" class="catalog-empty">Todavía no hay ninguno.</td></tr>';
    items.forEach((item) => {
      const row = document.createElement("tr");
      if (!item.is_active) row.className = "catalog-row-inactive";
      const cells = catalog.columns(item).map((html) => `<td>${html}</td>`).join("");
      row.innerHTML = `${cells}<td>${item.is_active ? "Activo" : "Desactivado"}</td><td><div class="catalog-row-actions"></div></td>`;
      const actions = row.querySelector(".catalog-row-actions");
      actions.appendChild(rowButton("Editar", "btn-outline", () => startEdit(catalog, item)));
      if (item.is_active) {
        actions.appendChild(rowButton("Desactivar", "btn-outline", () => deactivate(catalog, item)));
      } else {
        actions.appendChild(rowButton("Activar", "btn-primary", () => activate(catalog, item)));
      }
      body.appendChild(row);
    });
  } catch (err) {
    if (err.isSessionExpired) return;
    body.innerHTML = `<tr><td colspan="4" class="catalog-empty">${escapeHtml(err.message)}</td></tr>`;
  }
}

function startEdit(catalog, item) {
  document.getElementById(catalog.idInput).value = item.id;
  catalog.fill(item);
  document.getElementById(catalog.submit).textContent = "Guardar";
  document.getElementById(catalog.cancel).hidden = false;
}

function resetForm(catalog) {
  document.getElementById(catalog.form).reset();
  document.getElementById(catalog.idInput).value = "";
  document.getElementById(catalog.submit).textContent = "Agregar";
  document.getElementById(catalog.cancel).hidden = true;
}

async function save(catalog, event) {
  event.preventDefault();
  const id = document.getElementById(catalog.idInput).value;
  try {
    const saved = await send(
      id ? `${catalog.url}${id}/` : catalog.url,
      id ? "PATCH" : "POST",
      catalog.read(),
      "No se pudo guardar."
    );
    resetForm(catalog);
    await load(catalog);
    showMessage(`${catalog.noun} «${saved.name}» ${id ? "guardado" : "agregado"}.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function deactivate(catalog, item) {
  if (!window.confirm(`¿Desactivar «${item.name}»? Deja de ofrecerse; lo que ya lo usa no cambia.`)) return;
  try {
    await send(`${catalog.url}${item.id}/`, "DELETE", null, "No se pudo desactivar.");
    await load(catalog);
    showMessage(`«${item.name}» desactivado.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

async function activate(catalog, item) {
  try {
    await send(`${catalog.url}${item.id}/`, "PATCH", { is_active: true }, "No se pudo activar.");
    await load(catalog);
    showMessage(`«${item.name}» activado de nuevo.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message);
  }
}

function bootstrap() {
  if (!canUseUserPermission("catalogs.manage")) {
    window.location.replace("dashboard.html");
    return;
  }
  Object.values(CATALOGS).forEach((catalog) => {
    document.getElementById(catalog.form).addEventListener("submit", (event) => save(catalog, event));
    document.getElementById(catalog.cancel).addEventListener("click", () => resetForm(catalog));
    load(catalog);
  });
}

bootstrap();
