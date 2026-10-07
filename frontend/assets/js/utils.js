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
          <p class="empty-state">Cargando pedidos...</p>
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
