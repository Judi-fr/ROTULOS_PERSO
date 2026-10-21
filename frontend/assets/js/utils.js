// Funciones compartidas por varios scripts del frontend (antes copiadas y
// pegadas en cada uno, con pequeñas diferencias entre copias). Se carga
// como script CLÁSICO, siempre después de config.js/auth.js y antes del
// script propio de cada página: sus `function` de nivel superior quedan
// como globales (propiedades de `window`), igual que auth.js.
//
// Un módulo ES (p. ej. assets/js/labels_api.js) no puede recibir un script
// clásico "por delante" en el sentido de import, pero sí lee estas
// funciones desde `window` una vez cargado este archivo (mismo criterio
// que ese módulo ya usa con `window.Auth`, ver auth.js).

// Escapa texto para insertarlo en HTML (texto de nodo O valor de atributo:
// por eso escapa también comillas). Antes había una versión más corta
// (armaba un <div>, le asignaba textContent y leía innerHTML) en un par de
// páginas que NO escapaba comillas/apóstrofes — funcionaba para texto de
// nodo, pero al usarse dentro de un atributo (p. ej. `title="${escapeHtml(x)}"`
// en integraciones.js, con texto de terceros como el cuerpo de un webhook)
// una comilla sin escapar podía cerrar el atributo. Esta versión escapa
// las cinco entidades y es segura en ambos contextos.
function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]
  );
}

// Arma un mensaje de error legible a partir de una respuesta de error de la
// API: string plano, {detail: "..."}, {detail: ["...", "..."]} (varios
// errores) o el primer error de un serializer ({campo: ["error"]}).
function getErrorMessage(data, fallback) {
  if (typeof data === "string") return data;
  if (!data || typeof data !== "object") return fallback;
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) return data.detail.join(" ");
  for (const value of Object.values(data)) {
    if (Array.isArray(value) && value.length) return String(value[0]);
    if (typeof value === "string") return value;
  }
  return fallback;
}

// Muestra un mensaje en el <p id="pageMessage" class="page-message"> de la
// página (el mismo elemento/clase en todas las páginas que lo usan; solo
// cambia si arrancan ocultas con `hidden` o con `style="display:none"` —
// esta función cubre ambos casos). dashboard.html tiene su propio elemento
// (id/clase distintos) y su propio showMessage local: no lo reemplaza este.
// Si el aviso quedó fuera de la vista (la acción se hizo más abajo en la
// página), se lleva la página hasta él: un aviso que no se ve es como no
// avisar.
// Si la sesión guardada tiene el permiso. Solo para no ofrecer botones de más
// en las páginas del comercio (las del admin usan canUseUserPermission de
// admin_common.js); el backend vuelve a decidir. Sin lista de permisos (una
// sesión vieja) no se esconde nada.
function sessionHasPermission(key) {
  const user = window.Auth?.getCurrentUser?.();
  const permissions = user && user.permissions;
  return Array.isArray(permissions) ? permissions.includes(key) : true;
}

function showMessage(text, type = "error") {
  const el = document.getElementById("pageMessage");
  if (!el) return;
  el.textContent = text;
  el.className = `page-message ${type}`;
  el.hidden = false;
  el.style.display = "block";
  const box = el.getBoundingClientRect();
  if (box.top < 0 || box.bottom > window.innerHeight) {
    el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

// Formatea una fecha ISO como dd/mm/aaaa hh:mm (es-AR). "-" si no hay
// fecha o no se puede parsear. No es la única función de fecha del
// frontend a propósito: pedidos.js necesita solo la fecha (sin hora) y
// tiendas.js usa un formato corto de Intl distinto — moverlas acá les
// cambiaría lo que ve el usuario, así que quedaron como funciones propias
// de esas páginas. admin_common.js expone su propia formatDateTime (con
// guarda NaN en vez de try/catch) para las páginas de administración.
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
    return "-";
  }
}

// La API pagina (PageNumberPagination): {count, next, previous, results}.
// Esta función normaliza tanto una respuesta paginada como un array plano.
function extractResults(data) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.results)) return data.results;
  return [];
}

// Descarga un archivo que devolvió la API (export, PDF) sin abrir otra
// pestaña. El nombre sale del Content-Disposition del backend si lo trae.
async function downloadResponse(response, fallbackName) {
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = match ? match[1] : fallbackName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// Selector de pedidos propios con filtros (tienda, estado, rango de fechas),
// paginado y con selección que sobrevive al cambio de página y de filtro.
// Lo comparten las acciones masivas que trabajan sobre pedidos tildados
// (planilla_retiro.html, estado_pedidos.html); dibuja sus dos tarjetas
// dentro de `root`. Usa las clases de imprimir_rotulos.css, que esas
// páginas cargan. Necesita window.Auth (auth.js).
//
//   const picker = createOrderPicker(root, {
//     statusOptions: [["created,preparing", "Pendientes de despacho"], ...],
//     onSelectionChange: (count) => ...,
//   });
//   picker.selectedIds(); picker.clear(); picker.reload();
function createOrderPicker(root, options = {}) {
  const apiBase = window.APP_CONFIG.API_BASE;
  const statusOptions = options.statusOptions || [
    ["created,preparing", "Pendientes de despacho"],
    ["", "Todos"],
    ["created", "Creados"],
    ["preparing", "En preparación"],
    ["dispatched", "Despachados"],
    ["in_transit", "En tránsito"],
  ];
  const selected = new Set();
  let page = 1;
  let hasNext = false;

  root.innerHTML = `
    <section class="profile-card">
      <div class="profile-card-title">Qué pedidos ver</div>
      <div class="profile-card-body">
        <div class="filters-row">
          <div class="field" data-role="storeField" style="display:none;">
            <label>Tienda</label>
            <select data-role="store"><option value="">Todas las tiendas</option></select>
          </div>
          <div class="field">
            <label>Estado</label>
            <select data-role="status"></select>
          </div>
          <div class="field">
            <label>Desde</label>
            <input type="date" data-role="dateFrom" />
          </div>
          <div class="field">
            <label>Hasta</label>
            <input type="date" data-role="dateTo" />
          </div>
        </div>
        <p class="filters-hint">
          El rango acota por fecha de alta del pedido. Filtrá y usá
          "Seleccionar todos" para no tildarlos de a uno.
        </p>
      </div>
    </section>
    <section class="profile-card">
      <div class="profile-card-title">
        <span>Pedidos</span>
        <span class="selection-count" data-role="count">0 seleccionados</span>
      </div>
      <div class="profile-card-body">
        <div class="select-all-row">
          <input type="checkbox" data-role="selectAll" id="pickerSelectAll" />
          <label for="pickerSelectAll">Seleccionar todos los de esta página</label>
        </div>
        <div data-role="list" class="print-order-list">
          <p class="empty-state"><span class="app-skeleton" role="status" aria-label="Cargando…"><span></span><span></span><span></span></span></p>
        </div>
        <div class="pager" data-role="pager" style="display:none;">
          <button type="button" class="btn btn-outline btn-small" data-role="prev">Anterior</button>
          <span data-role="pageInfo"></span>
          <button type="button" class="btn btn-outline btn-small" data-role="next">Siguiente</button>
        </div>
      </div>
    </section>
  `;
  const el = (role) => root.querySelector(`[data-role="${role}"]`);
  statusOptions.forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    el("status").appendChild(option);
  });

  function updateCount() {
    const count = selected.size;
    el("count").textContent = count === 1 ? "1 seleccionado" : `${count} seleccionados`;
    if (options.onSelectionChange) options.onSelectionChange(count);
  }

  function addressSummary(address) {
    if (!address) return "";
    const street = [address.street, address.number].filter(Boolean).join(" ");
    const city = [address.city, address.state].filter(Boolean).join(", ");
    return [street, city].filter(Boolean).join(" — ");
  }

  // Todo lo que viene de una tienda es texto de terceros: se escapa siempre.
  function render(orders) {
    const list = el("list");
    if (!orders.length) {
      list.innerHTML = '<p class="empty-state">No hay pedidos con estos filtros.</p>';
      return;
    }
    list.innerHTML = "";
    orders.forEach((order) => {
      const row = document.createElement("label");
      row.className = "print-order";
      const number = order.external_number || order.id;
      const recipient = order.address?.recipient_name || "";
      const store = order.store_connection
        ? `<span class="store-tag">${escapeHtml(order.store_name || "Tienda")}</span>`
        : "";
      const tracking = order.tracking_number
        ? ` · Seguimiento ${escapeHtml(order.tracking_number)}`
        : "";
      row.innerHTML = `
        <input type="checkbox" value="${escapeHtml(order.id)}" ${selected.has(order.id) ? "checked" : ""} />
        <span class="print-order-body">
          <span class="print-order-title">Pedido #${escapeHtml(number)}${store}</span>
          <span class="print-order-meta">${escapeHtml(recipient)}${recipient ? " · " : ""}${escapeHtml(addressSummary(order.address))}</span>
          <span class="print-order-meta">${escapeHtml(formatDate(order.created_at))} · ${escapeHtml(order.status_label || order.status)}${tracking}</span>
        </span>
      `;
      row.querySelector("input").addEventListener("change", (event) => {
        if (event.target.checked) selected.add(order.id);
        else selected.delete(order.id);
        updateCount();
      });
      list.appendChild(row);
    });
  }

  async function load() {
    const params = new URLSearchParams({ page: String(page) });
    const filters = currentFilters();
    Object.entries(filters).forEach(([key, value]) => params.set(key, value));
    try {
      const response = await window.Auth.apiFetch(`${apiBase}/orders/?${params}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar los pedidos."));
      render(extractResults(data));
      hasNext = Boolean(data.next);
      el("pager").style.display = hasNext || page > 1 ? "flex" : "none";
      el("pageInfo").textContent = `Página ${page}`;
      el("prev").disabled = page <= 1;
      el("next").disabled = !hasNext;
      el("selectAll").checked = false;
    } catch (err) {
      if (err.isSessionExpired) return;
      el("list").innerHTML = `<p class="empty-state">${escapeHtml(err.message)}</p>`;
    }
  }

  function currentFilters() {
    const filters = {};
    const map = { store: "store", status: "status", date_from: "dateFrom", date_to: "dateTo" };
    Object.entries(map).forEach(([key, role]) => {
      const value = el(role).value;
      if (value) filters[key] = value;
    });
    return filters;
  }

  async function loadStores() {
    try {
      const response = await window.Auth.apiFetch(`${apiBase}/integrations/stores/`);
      if (!response.ok) return;
      const stores = extractResults(await response.json());
      if (!stores.length) return;
      const select = el("store");
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
      el("storeField").style.display = "";
    } catch (err) {
      if (!err.isSessionExpired) console.error("Error al cargar tiendas:", err);
    }
  }

  ["store", "status", "dateFrom", "dateTo"].forEach((role) => {
    el(role).addEventListener("change", () => {
      page = 1;
      load();
    });
  });
  el("selectAll").addEventListener("change", (event) => {
    el("list").querySelectorAll("input[type=checkbox]").forEach((checkbox) => {
      checkbox.checked = event.target.checked;
      const id = Number(checkbox.value);
      if (event.target.checked) selected.add(id);
      else selected.delete(id);
    });
    updateCount();
  });
  el("prev").addEventListener("click", () => {
    if (page > 1) {
      page -= 1;
      load();
    }
  });
  el("next").addEventListener("click", () => {
    if (hasNext) {
      page += 1;
      load();
    }
  });

  loadStores().then(load);
  updateCount();

  return {
    selectedIds: () => Array.from(selected),
    clear() {
      selected.clear();
      updateCount();
    },
    reload: load,
    filters: currentFilters,
  };
}

// Detalle de una acción masiva ({updated, failed, results: [{number, ok,
// detail}]}, ver apps.orders.bulk_views): lista solo los pedidos que NO se
// pudieron actualizar y por qué. Los que salieron bien ya los resume el
// aviso de la página.
function renderBulkResult(container, data) {
  container.innerHTML = "";
  const failed = (data?.results || []).filter((item) => !item.ok);
  if (!failed.length) return;
  const box = document.createElement("div");
  box.className = "bulk-result";
  const title = document.createElement("p");
  title.className = "bulk-result-title";
  title.textContent =
    failed.length === 1 ? "1 pedido no se actualizó:" : `${failed.length} pedidos no se actualizaron:`;
  const list = document.createElement("ul");
  failed.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = `#${item.number}: ${item.detail}`;
    list.appendChild(li);
  });
  box.append(title, list);
  container.appendChild(box);
}

// Datos de la cuenta (GET /auth/me/account/, organizacion.html): las
// preferencias que otras pantallas usan como valor inicial. Una sola consulta
// por página; sin permiso o sin datos devuelve {}.
let accountSettingsPromise = null;
function loadAccountSettings() {
  if (!accountSettingsPromise) {
    accountSettingsPromise = window.Auth.apiFetch(`${window.APP_CONFIG.API_BASE}/auth/me/account/`)
      .then((response) => (response.ok ? response.json() : {}))
      .catch((err) => {
        if (err.isSessionExpired) throw err;
        return {};
      });
  }
  return accountSettingsPromise;
}

// Sugerencias para un campo de texto de transportista: los del catálogo
// global (transportistas sin API, catalogos.html) y los que tienen API. Sigue
// siendo texto libre: es una ayuda para escribir siempre igual el mismo
// nombre, y al despachar el backend lo normaliza al del catálogo.
let carrierNamesPromise = null;
function loadCarrierNames() {
  if (!carrierNamesPromise) {
    const base = window.APP_CONFIG.API_BASE;
    const read = (url) =>
      window.Auth.apiFetch(url)
        .then((response) => (response.ok ? response.json() : []))
        .catch(() => []);
    carrierNamesPromise = Promise.all([read(`${base}/carriers/manual-carriers/`), read(`${base}/carriers/`)]).then(
      ([manual, withApi]) => {
        const names = [...extractResults(manual), ...extractResults(withApi)]
          .map((carrier) => carrier.name)
          .filter(Boolean);
        return [...new Set(names)].sort((a, b) => a.localeCompare(b, "es"));
      }
    );
  }
  return carrierNamesPromise;
}

async function attachCarrierSuggestions(input) {
  if (!input) return;
  const names = await loadCarrierNames();
  let list = document.getElementById("carrierSuggestions");
  if (!list) {
    list = document.createElement("datalist");
    list.id = "carrierSuggestions";
    document.body.appendChild(list);
  }
  list.innerHTML = names.map((name) => `<option value="${escapeHtml(name)}"></option>`).join("");
  input.setAttribute("list", list.id);
  input.setAttribute("autocomplete", "off");
}

// --- Comercios con equipo (equipo.html, perfiles_equipo.html) ---------------
// Los accesos que el dueño reparte vienen del backend (GET /team/ → accesses,
// {clave: etiqueta}); acá solo se dibujan como tildes y se leen.

// Dibuja una tilde por acceso dentro de ``container``. ``selected``: claves
// tildadas. ``options.allowed``: si viene, las que no están ahí salen
// deshabilitadas (no se puede dar lo que uno no tiene; el backend lo vuelve
// a chequear).
function renderAccessCheckboxes(container, accesses, selected, options = {}) {
  const chosen = new Set(selected || []);
  const allowed = options.allowed ? new Set(options.allowed) : null;
  container.innerHTML = "";
  container.classList.add("access-grid");
  Object.entries(accesses || {}).forEach(([key, label]) => {
    const row = document.createElement("label");
    row.className = "access-option";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = key;
    box.checked = chosen.has(key);
    box.disabled = Boolean(options.disabled) || (allowed !== null && !allowed.has(key));
    const text = document.createElement("span");
    text.textContent = label;
    row.append(box, text);
    container.appendChild(row);
  });
}

function readAccessCheckboxes(container) {
  return [...container.querySelectorAll("input[type=checkbox]:checked")].map((box) => box.value);
}

// "Todas las tiendas" o algunas. ``stores``: [{id, name, platform}].
function renderStoreChoice(container, stores, choice = {}) {
  const allStores = choice.allStores !== false;
  const ids = new Set(choice.storeIds || []);
  const name = `stores-${Math.random().toString(36).slice(2)}`;
  container.innerHTML = `
    <label class="access-option"><input type="radio" name="${name}" value="all" ${allStores ? "checked" : ""} /> <span>Todas las tiendas (también las que se conecten después)</span></label>
    <label class="access-option"><input type="radio" name="${name}" value="some" ${allStores ? "" : "checked"} /> <span>Solo algunas</span></label>
    <div class="store-pick" ${allStores ? "hidden" : ""}></div>`;
  const pick = container.querySelector(".store-pick");
  if (!stores || !stores.length) {
    pick.innerHTML = '<p class="access-muted">El comercio todavía no tiene tiendas conectadas.</p>';
  }
  (stores || []).forEach((store) => {
    const row = document.createElement("label");
    row.className = "access-option";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.value = store.id;
    box.checked = ids.has(store.id);
    const text = document.createElement("span");
    text.textContent = store.name || `Tienda ${store.id}`;
    row.append(box, text);
    pick.appendChild(row);
  });
  container.querySelectorAll(`input[name=${name}]`).forEach((radio) =>
    radio.addEventListener("change", () => {
      pick.hidden = container.querySelector(`input[name=${name}]:checked`).value === "all";
    })
  );
}

function readStoreChoice(container) {
  const all = container.querySelector("input[type=radio]:checked")?.value !== "some";
  const storeIds = all
    ? []
    : [...container.querySelectorAll(".store-pick input:checked")].map((box) => Number(box.value));
  return { all_stores: all, store_ids: storeIds };
}

// ---------------------------------------------------------------------------
// Diálogos propios en vez de alert()/confirm()/prompt() del navegador: la
// ventana gris del navegador no se parece a la app, en algunos navegadores
// trae "Esta página dice…" con la dirección y se puede silenciar para
// siempre. Devuelven promesas, así que se usan con await:
//   if (!(await appConfirm("¿Eliminar «X»?"))) return;
// Sin `confirmLabel`, el botón toma el verbo de la pregunta ("¿Eliminar…"
// → "Eliminar") y es rojo si es uno de los que borran o cortan algo. El
// botón para no hacer nada dice "Volver" y no "Cancelar": varias preguntas
// son "¿Cancelar el pedido?" y quedarían dos botones "Cancelar".
// Los estilos están en assets/css/ui.css, que cargan todas las páginas.
// ---------------------------------------------------------------------------
const APP_DIALOG_DANGER_VERBS = [
  "eliminar", "borrar", "quitar", "cancelar", "desactivar", "archivar",
  "revocar", "desconectar", "dejar", "mover", "transferir",
];

// Arma y abre el diálogo; resuelve con el valor del botón tocado (o con
// `cancelValue` si se cierra con Esc).
function openAppDialog({ title, text, tone, buttons, input, cancelValue }) {
  return new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = `app-dialog${tone ? ` app-dialog-${tone}` : ""}`;
    const body = document.createElement("div");
    body.className = "app-dialog-body";
    if (title) {
      const h = document.createElement("h2");
      h.className = "app-dialog-title";
      h.textContent = title;
      body.appendChild(h);
    }
    if (text) {
      const p = document.createElement("p");
      p.className = "app-dialog-text";
      p.textContent = text;
      body.appendChild(p);
    }
    let field = null;
    if (input) {
      field = document.createElement("input");
      field.type = "text";
      field.className = "app-dialog-input";
      field.value = input.value || "";
      field.setAttribute("aria-label", title || text || "Valor");
      body.appendChild(field);
    }
    const actions = document.createElement("div");
    actions.className = "app-dialog-actions";
    let primary = null;
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      dialog.close();
      dialog.remove();
      resolve(value);
    };
    buttons.forEach((b) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `app-dialog-btn app-dialog-btn-${b.kind}`;
      btn.textContent = b.label;
      btn.addEventListener("click", () => finish(b.value === "input" ? field.value : b.value));
      actions.appendChild(btn);
      if (b.kind !== "secondary") primary = btn;
    });
    dialog.append(body, actions);
    dialog.addEventListener("cancel", (e) => {
      e.preventDefault();
      finish(cancelValue);
    });
    if (field) {
      field.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          finish(field.value);
        }
      });
    }
    document.body.appendChild(dialog);
    dialog.showModal();
    if (field) {
      field.focus();
      field.select();
    } else if (primary) {
      primary.focus();
    }
  });
}

// Separa "¿Pregunta?\n\nDetalle" en título y texto, para que la pregunta
// se lea como título y el detalle debajo.
function splitDialogMessage(message) {
  const text = String(message ?? "");
  const cut = text.indexOf("\n\n");
  if (cut > 0) return { title: text.slice(0, cut), text: text.slice(cut + 2) };
  const q = text.indexOf("?");
  if (text.startsWith("¿") && q > 0 && q < text.length - 1) {
    return { title: text.slice(0, q + 1), text: text.slice(q + 1).trim() };
  }
  return { title: text, text: "" };
}

function appConfirm(message, options = {}) {
  const { title, text } = splitDialogMessage(message);
  const verb = (String(message).match(/^¿\s*([A-Za-zÁÉÍÓÚáéíóúñÑ]+)/) || [])[1] || "";
  const danger = options.danger ?? APP_DIALOG_DANGER_VERBS.includes(verb.toLowerCase());
  const label = options.confirmLabel || (verb ? verb[0].toUpperCase() + verb.slice(1).toLowerCase() : "Aceptar");
  return openAppDialog({
    title,
    text,
    cancelValue: false,
    buttons: [
      { label: options.cancelLabel || "Volver", kind: "secondary", value: false },
      { label, kind: danger ? "danger" : "primary", value: true },
    ],
  });
}

// Aviso con un solo botón. `tone`: "error" (por defecto) | "success" | "info".
function appAlert(message, options = {}) {
  const tone = options.tone || "error";
  const { title, text } = splitDialogMessage(message);
  return openAppDialog({
    title: options.title || title,
    text: options.title ? String(message) : text,
    tone,
    cancelValue: undefined,
    buttons: [{ label: "Entendido", kind: "primary", value: undefined }],
  });
}

// Pide un texto; resuelve con el texto o con null si se vuelve atrás (igual
// que window.prompt).
function appPrompt(message, defaultValue = "", options = {}) {
  return openAppDialog({
    title: message,
    text: options.text || "",
    input: { value: defaultValue },
    cancelValue: null,
    buttons: [
      { label: "Volver", kind: "secondary", value: null },
      { label: options.confirmLabel || "Aceptar", kind: "primary", value: "input" },
    ],
  });
}

// ---------------------------------------------------------------------------
// Estados de carga y de lista vacía (estilos en assets/css/ui.css).
// appSkeleton(): barras grises animadas mientras llega la respuesta, en vez
// de un "Cargando..." suelto. appEmptyState(): qué significa que no haya nada
// y, cuando corresponde, el botón para empezar.
// ---------------------------------------------------------------------------
function appSkeleton() {
  return '<span class="app-skeleton" role="status" aria-label="Cargando…"><span></span><span></span><span></span></span>';
}

// Íconos de línea propios (no todas las páginas cargan la fuente de íconos).
const APP_EMPTY_ICONS = {
  box: '<path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5z"/><path d="M3 7.5 12 12l9-4.5M12 12v9"/>',
  store: '<path d="M4 9.5V20h16V9.5"/><path d="M3 9.5 5 4h14l2 5.5a3 3 0 0 1-6 0 3 3 0 0 1-6 0 3 3 0 0 1-6 0z"/><path d="M10 20v-5h4v5"/>',
  printer: '<path d="M7 8V3h10v5"/><rect x="3" y="8" width="18" height="9" rx="2"/><path d="M7 14h10v7H7z"/>',
  doc: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.7 2.7L16 10"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/><circle cx="4.5" cy="18" r="1"/>',
};

function appEmptyState({ icon = "list", title, text = "", actionLabel = "", actionHref = "" } = {}) {
  const action = actionLabel && actionHref
    ? `<a class="app-empty-action" href="${escapeHtml(actionHref)}">${escapeHtml(actionLabel)}</a>`
    : "";
  const svg = APP_EMPTY_ICONS[icon] || APP_EMPTY_ICONS.list;
  return `<div class="app-empty">
    <span class="app-empty-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="26" height="26" fill="none"
      stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${svg}</svg></span>
    <p class="app-empty-title">${escapeHtml(title)}</p>
    ${text ? `<p class="app-empty-text">${escapeHtml(text)}</p>` : ""}
    ${action}
  </div>`;
}

// ---------------------------------------------------------------------------
// Botón ocupado mientras se envía un formulario. Muchos formularios no
// bloqueaban su botón: un doble clic creaba dos veces lo mismo y nada decía
// que se estaba guardando. Esto lo hace para TODOS los formularios que no lo
// hacen ya por su cuenta:
// - corre en la fase de burbuja del documento, o sea DESPUÉS del manejador
//   propio del formulario; si ese manejador ya desactivó el botón (los que
//   ponen su propio "Conectando…"), no se toca nada;
// - si el formulario se maneja por JS (preventDefault), el botón queda
//   desactivado con un texto según la acción hasta que terminan los pedidos
//   al servidor que arrancaron con él (se cuentan los fetch en curso), o
//   enseguida si no arrancó ninguno (p. ej. falló una validación).
// ---------------------------------------------------------------------------
(function setupBusySubmitButtons() {
  if (window.__busySubmitReady || typeof window.fetch !== "function") return;
  window.__busySubmitReady = true;

  let pending = 0;
  const busyButtons = new Set();
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (...args) => {
    pending += 1;
    return nativeFetch(...args).finally(() => {
      pending -= 1;
      setTimeout(releaseIfIdle, 0);
    });
  };

  function busyLabel(text) {
    const t = text.toLowerCase();
    if (/buscar|filtrar/.test(t)) return "Buscando…";
    if (/conectar|vincular/.test(t)) return "Conectando…";
    if (/ingresar|iniciar|entrar/.test(t)) return "Ingresando…";
    if (/invitar|enviar|mandar/.test(t)) return "Enviando…";
    if (/crear|agregar|generar|registr/.test(t)) return "Creando…";
    if (/importar|subir|cargar/.test(t)) return "Subiendo…";
    return "Guardando…";
  }

  function releaseIfIdle() {
    if (pending > 0) return;
    busyButtons.forEach((button) => {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      if (button.dataset.idleLabel !== undefined) {
        button.textContent = button.dataset.idleLabel;
        delete button.dataset.idleLabel;
      }
    });
    busyButtons.clear();
  }

  document.addEventListener("submit", (event) => {
    if (!event.defaultPrevented) return; // envío normal del navegador: se va de la página
    const button = event.submitter || event.target.querySelector('[type="submit"]');
    if (!button || button.disabled || busyButtons.has(button)) return;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    // Solo se cambia el texto de botones de texto plano (sin ícono adentro).
    if (button.tagName === "BUTTON" && !button.children.length && button.textContent.trim()) {
      button.dataset.idleLabel = button.textContent;
      button.textContent = busyLabel(button.textContent);
    }
    busyButtons.add(button);
    // Si el manejador no pidió nada al servidor, se libera enseguida.
    setTimeout(releaseIfIdle, 60);
  });
})();

// --- Editor de perfiles del equipo (2026-10-21) ---------------------------
// El mismo diseño que roles.html: perfiles a la izquierda (con "Nuevo
// perfil" al pie), y a la derecha el nombre y los accesos del elegido, con
// Guardar/Descartar que solo se activan con cambios. Lo usan la pestaña
// Perfiles de equipo.html (el comercio) y roles.html → Perfiles de los
// comercios (el admin, para cualquier comercio). Quien lo usa pone los datos
// y las llamadas a la API:
//   catalog  {clave: "Ver pedidos", ...}
//   allowed  claves que puede dar quien edita, o null (todas)
//   save(profile|null, {name, accesses}) → perfil guardado (null = crear)
//   remove(profile) → promesa
// Devuelve {update(profiles)} para dibujar la lista (conserva el elegido).
function createProfileEditor(root, { catalog, allowed = null, save, remove }) {
  root.classList.add("pe");
  root.innerHTML = `
    <aside class="pe-card pe-side">
      <div class="pe-title">Perfiles</div>
      <div class="pe-list" role="list"></div>
      <form class="pe-create">
        <label>Nuevo perfil</label>
        <div class="pe-create-row">
          <input type="text" maxlength="60" placeholder="Ej: Depósito" />
          <button type="submit" class="pe-btn pe-btn-primary">Crear</button>
        </div>
      </form>
    </aside>
    <section class="pe-card pe-detail">
      <div class="pe-detail-head">
        <label class="pe-name-label">Nombre
          <input type="text" class="pe-name" maxlength="60" />
        </label>
        <span class="pe-count"></span>
      </div>
      <div class="pe-toolbar"><button type="button" class="pe-link pe-toggle-all"></button></div>
      <div class="pe-accesses"></div>
      <div class="pe-actions">
        <span class="pe-dirty" hidden>Hay cambios sin guardar</span>
        <button type="button" class="pe-btn pe-btn-danger pe-delete">Borrar perfil</button>
        <button type="button" class="pe-btn pe-discard">Descartar</button>
        <button type="button" class="pe-btn pe-btn-primary pe-save">Guardar cambios</button>
      </div>
    </section>`;
  const listEl = root.querySelector(".pe-list");
  const detail = root.querySelector(".pe-detail");
  const nameInput = root.querySelector(".pe-name");
  const accessesEl = root.querySelector(".pe-accesses");
  const countEl = root.querySelector(".pe-count");
  const toggleAll = root.querySelector(".pe-toggle-all");
  const saveBtn = root.querySelector(".pe-save");
  const discardBtn = root.querySelector(".pe-discard");
  const deleteBtn = root.querySelector(".pe-delete");
  const dirtyNote = root.querySelector(".pe-dirty");
  let profiles = [];
  let selected = null;

  const boxes = () => [...accessesEl.querySelectorAll("input")];
  const editable = () => boxes().filter((box) => !box.disabled);

  function isDirty() {
    if (!selected) return false;
    const now = readAccessCheckboxes(accessesEl).sort().join();
    return nameInput.value.trim() !== selected.name || now !== [...selected.accesses].sort().join();
  }

  function refresh() {
    const total = boxes().length;
    countEl.textContent = selected ? `${readAccessCheckboxes(accessesEl).length} de ${total} accesos` : "";
    const free = editable();
    toggleAll.hidden = !free.length;
    toggleAll.textContent = free.length && free.every((box) => box.checked) ? "Desmarcar todos" : "Marcar todos";
    const dirty = isDirty();
    saveBtn.disabled = !dirty;
    discardBtn.disabled = !dirty;
    dirtyNote.hidden = !dirty;
  }

  function show(profile) {
    selected = profile;
    detail.hidden = !profile;
    listEl.querySelectorAll(".pe-item").forEach((item) => {
      item.classList.toggle("active", Boolean(profile) && item.dataset.id === String(profile.id));
    });
    if (!profile) return;
    nameInput.value = profile.name;
    renderAccessCheckboxes(accessesEl, catalog, profile.accesses, { allowed });
    accessesEl.querySelectorAll("input").forEach((box) => box.addEventListener("change", refresh));
    refresh();
  }

  async function choose(profile) {
    if (selected && profile && selected.id === profile.id) return;
    if (isDirty() && !(await appConfirm("¿Descartar los cambios sin guardar de este perfil?", { confirmLabel: "Descartar" }))) return;
    show(profile);
  }

  function renderList() {
    listEl.innerHTML = "";
    if (!profiles.length) {
      listEl.innerHTML = '<p class="pe-empty">Todavía no hay perfiles.</p>';
    }
    profiles.forEach((profile) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "pe-item";
      item.dataset.id = profile.id;
      const name = document.createElement("span");
      name.textContent = profile.name;
      const meta = document.createElement("small");
      const people = profile.member_count === 1 ? "1 persona" : `${profile.member_count || 0} personas`;
      meta.textContent = `${profile.accesses.length} accesos · ${people}`;
      item.append(name, meta);
      item.addEventListener("click", () => choose(profile));
      listEl.appendChild(item);
    });
  }

  toggleAll.addEventListener("click", () => {
    const free = editable();
    const check = !free.every((box) => box.checked);
    free.forEach((box) => (box.checked = check));
    refresh();
  });
  nameInput.addEventListener("input", refresh);
  discardBtn.addEventListener("click", () => show(selected));
  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    try {
      const saved = await save(selected, { name: nameInput.value.trim(), accesses: readAccessCheckboxes(accessesEl) });
      showMessage(`Perfil «${saved.name}» guardado.`, "success");
    } catch (err) {
      if (!err.isSessionExpired) showMessage(err.message);
      refresh();
    }
  });
  deleteBtn.addEventListener("click", async () => {
    const target = selected;
    if (!target || !(await appConfirm(`¿Borrar el perfil «${target.name}»?`))) return;
    try {
      await remove(target);
      showMessage(`Perfil «${target.name}» borrado.`, "success");
    } catch (err) {
      if (!err.isSessionExpired) showMessage(err.message);
    }
  });
  root.querySelector(".pe-create").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = event.target.querySelector("input");
    const name = input.value.trim();
    if (!name) return;
    try {
      const created = await save(null, { name, accesses: [] });
      input.value = "";
      showMessage(`Perfil «${created.name}» creado: elegí sus accesos.`, "success");
      const fresh = profiles.find((profile) => profile.id === created.id);
      if (fresh) show(fresh);
    } catch (err) {
      if (!err.isSessionExpired) showMessage(err.message);
    }
  });

  return {
    update(next) {
      profiles = next || [];
      const keepId = selected && selected.id;
      renderList();
      show(profiles.find((profile) => profile.id === keepId) || profiles[0] || null);
    },
    hasUnsavedChanges: isDirty,
  };
}
