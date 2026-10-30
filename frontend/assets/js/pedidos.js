// Mis pedidos: la lista de pedidos (de las tiendas y cargados a mano) y el
// diálogo para cargar uno a mano. Las direcciones propias tienen su página
// (direcciones.html, assets/js/direcciones.js); acá solo se leen para el diálogo.
// Sesión y apiFetch salen de assets/js/auth.js (window.Auth), compartido con
// el resto del frontend (ver dashboard.js / perfil.js / usuarios.js).

const AUTH_BASE = `${window.APP_CONFIG.API_BASE}/auth`;
const API_BASE = window.APP_CONFIG.API_BASE;
const ME_URL = `${AUTH_BASE}/me/`;
const ADDRESSES_URL = `${API_BASE}/addresses/`;
const ORDERS_URL = `${API_BASE}/orders/`;
const STORES_URL = `${API_BASE}/integrations/stores/`;

const STATUS_LABELS = {
  created: "Creado",
  preparing: "En preparación",
  dispatched: "Despachado",
  in_transit: "En tránsito",
  delivered: "Entregado",
  cancelled: "Cancelado",
};

const PLATFORM_LABELS = { tiendanube: "Tiendanube", shopify: "Shopify" };

let addresses = [];
// Paginación del historial: el backend pagina (PageNumberPagination) y antes
// se mostraba solo la primera página, así que un pedido viejo no aparecía por
// ningún lado. Se navega con next/previous, que es lo que manda el backend.
let ordersPage = 1;
let ordersHasNext = false;

// Vista que llega desde un número del panel (pedidos.html?vista=...): recorta
// la lista con los mismos filtros que usa el resumen del día
// (orders/summary_views.py), así lo que se ve es lo que se contó. Se quita
// con "Ver todos" del aviso de arriba de la lista.
const ORDER_VIEWS = {
  para_despachar: {
    label: "Pedidos con rótulo, listos para despachar",
    params: { status: "created,preparing", labels_printed: "1" },
    empty: "No hay pedidos con rótulo esperando el despacho.",
  },
  demorados: {
    label: "Pedidos pendientes que esperan hace más de 2 días",
    params: { status: "created,preparing", older_than_days: "2" },
    empty: "No hay pedidos demorados.",
  },
  en_camino: {
    label: "Pedidos en camino",
    params: { status: "dispatched,in_transit" },
    empty: "No hay pedidos en camino.",
  },
};
const requestedView = new URLSearchParams(location.search).get("vista");
let orderView = ORDER_VIEWS[requestedView] ? requestedView : "";

// Filtro por tienda: "" = todas, "manual" = cargados a mano, o el id de una
// tienda (el backend acepta ?store=<id>|manual). Puede llegar elegido desde el
// panel (pedidos.html?tienda=...).
let selectedStore = new URLSearchParams(location.search).get("tienda") || "";
// id de tienda -> número de color (store-color-N en pedidos.css). Se asigna
// por orden de alta (id), así cada tienda conserva su color aunque se sumen
// otras: las tiendas nunca se borran, solo se desconectan.
let storeColors = new Map();
// Ids de las tiendas en el orden de la columna: el mismo orden para los grupos.
let storeOrder = [];
const STORE_COLOR_COUNT = 6;
// Firma de la lista de tiendas ya dibujada, para no rearmar los botones si
// no cambió nada al volver a la pestaña.
let storesSignature = "";

const hasSession = () => window.Auth.hasSession();
const apiFetch = (url, options) => window.Auth.apiFetch(url, options);

// showMessage, getErrorMessage y extractResults salen de assets/js/utils.js.
// formatDate queda propia acá abajo: esta página no muestra la hora, solo
// la fecha (a diferencia de la versión de utils.js).

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

// ---------------------------------------------------------------------------
// DIRECCIONES
// ---------------------------------------------------------------------------

const NEW_ADDRESS = "__new__";

// "Enviar a": las direcciones guardadas y, al final, una nueva que se carga
// ahí mismo. Sin ninguna guardada, el formulario de la nueva directamente.
function renderAddressSelect() {
  const select = document.getElementById("orderAddressSelect");
  if (!select) return;
  const options = addresses.map((address) => {
    const who = address.recipient_name || address.label || "Sin nombre de quien recibe";
    return `<option value="${escapeHtml(address.id)}">${escapeHtml(who)} — ${escapeHtml(addressSummary(address))}</option>`;
  });
  options.push(`<option value="${NEW_ADDRESS}">+ Una dirección nueva…</option>`);
  select.innerHTML = options.join("");
  document.getElementById("orderAddressPick").hidden = addresses.length === 0;
  if (addresses.length === 0) select.value = NEW_ADDRESS;
  syncNewAddressBox();
}

function syncNewAddressBox() {
  const isNew = document.getElementById("orderAddressSelect").value === NEW_ADDRESS;
  document.getElementById("newAddressBox").hidden = !isNew;
}

async function loadAddresses() {
  try {
    const response = await apiFetch(ADDRESSES_URL);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    const data = await response.json();
    addresses = extractResults(data);
    renderAddressSelect();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar direcciones:", err);
    showFieldMessage("orderFormMsg", "No se pudieron cargar tus direcciones.", false);
  }
}

// ---------------------------------------------------------------------------
// PEDIDOS
// ---------------------------------------------------------------------------

// Los pedidos que vienen de una tienda online traen texto escrito por
// terceros (productos, direcciones del comprador): todo valor que va a
// innerHTML se escapa. escapeHtml sale de assets/js/utils.js.

function renderOrderList(orders) {
  const container = document.getElementById("orderList");
  if (!container) return;

  if (orders.length === 0) {
    // Con una tienda ya conectada no tiene sentido pedir que conecte una.
    const hasStores = storesSignature !== "" && storesSignature !== "[]";
    let empty = hasStores
      ? {
          icon: "box",
          title: "Todavía no hay pedidos",
          text: "Los de tus tiendas aparecen acá apenas entran. Para un envío por fuera, usá «+ Nuevo pedido», arriba.",
        }
      : {
          icon: "box",
          title: "Todavía no hay pedidos",
          text: "Conectá tu tienda y los pedidos entran solos, o cargá uno a mano con «+ Nuevo pedido», arriba.",
          actionLabel: "Conectar una tienda",
          actionHref: "tiendas.html",
        };
    if (selectedStore === "manual") empty = { icon: "box", title: "No hay pedidos cargados a mano" };
    else if (selectedStore) empty = { icon: "box", title: "No hay pedidos de esta tienda" };
    if (orderView) empty = { icon: "check-circle", title: ORDER_VIEWS[orderView].empty };
    container.innerHTML = appEmptyState(empty);
    return;
  }

  container.innerHTML = "";
  // En "Todas" los pedidos de la página se agrupan por tienda (en el orden de
  // la columna de tiendas, los cargados a mano al final), cada grupo con su
  // encabezado; dentro de cada grupo siguen del más nuevo al más viejo.
  // Filtrando una tienda no hace falta agrupar.
  const groups = selectedStore ? [{ storeId: null, orders }] : groupOrdersByStore(orders);
  groups.forEach((group) => {
    let target = container;
    if (!selectedStore) {
      const section = document.createElement("section");
      section.className = `order-group ${storeColorClass(group.storeId)}`;
      section.setAttribute("aria-label", group.label);
      section.appendChild(orderGroupHead(group));
      container.appendChild(section);
      target = section;
    }
    group.orders.forEach((order) => target.appendChild(orderItem(order)));
  });
}

function groupOrdersByStore(orders) {
  const byStore = new Map();
  orders.forEach((order) => {
    const key = order.store_connection || "manual";
    if (!byStore.has(key)) {
      byStore.set(key, {
        storeId: order.store_connection || null,
        label: order.store_connection ? order.store_name || "Tienda" : "Cargados a mano",
        platform: order.store_connection ? PLATFORM_LABELS[order.store_platform] || "" : "",
        orders: [],
      });
    }
    byStore.get(key).orders.push(order);
  });
  const rank = (group) => (group.storeId && storeColors.has(group.storeId) ? storeOrder.indexOf(group.storeId) : Infinity);
  return [...byStore.values()].sort((a, b) => rank(a) - rank(b));
}

function orderGroupHead(group) {
  const head = document.createElement("header");
  head.className = "order-group-head";
  const count = group.orders.length;
  head.innerHTML = `
    ${storeMonogram(group.storeId ? group.label : "", group.storeId ? "" : "manual")}
    <div class="order-group-name">
      <strong>${escapeHtml(group.label)}</strong>
      ${group.platform ? `<span>${escapeHtml(group.platform)}</span>` : ""}
    </div>
    <span class="order-group-count">${count} pedido${count === 1 ? "" : "s"}</span>
  `;
  return head;
}

// Iniciales de la tienda en un cuadrado de su color: es lo que la identifica
// de un vistazo en la columna de tiendas y en los grupos de pedidos.
function storeMonogram(name, kind = "") {
  if (kind === "all" || kind === "manual") {
    const icon = kind === "all" ? "squares-four" : "pencil-simple-line";
    return `<span class="store-mono store-mono-${kind}" aria-hidden="true"><i class="ph ph-${icon}"></i></span>`;
  }
  const words = String(name || "").split(/[\s\-_.]+/).filter(Boolean);
  const initials = (words.length > 1 ? words[0][0] + words[1][0] : (words[0] || "T").slice(0, 2)).toUpperCase();
  return `<span class="store-mono" aria-hidden="true">${escapeHtml(initials)}</span>`;
}

function orderItem(order) {
  const statusKey = order.status;
  const statusLabel = order.status_label || STATUS_LABELS[statusKey] || statusKey;
  // El historial de estados va plegado: el estado actual ya está en la
  // pastilla, y repetirlo como fila de chips en cada pedido solo hacía ruido.
  const events = order.status_events || [];
  const timeline = events
    .map((event) => `<li><span>${escapeHtml(event.status_label || STATUS_LABELS[event.status] || event.status)}</span>${event.created_at ? `<time>${escapeHtml(formatDate(event.created_at))}</time>` : ""}</li>`)
    .join("");

  // Despachar, seguimiento y etiqueta los agrega pedidos/envios.js (decorate).
  const actions = [];
  if (order.is_cancellable) {
    actions.push(
      `<button type="button" class="btn-danger-text order-cancel" data-cancel-order="${escapeHtml(order.id)}">Cancelar pedido</button>`
    );
  }

  // Pedido de tienda: se muestra el número que ve el comerciante en su
  // tienda (#1001); los manuales, su id propio. De qué tienda viene lo dicen
  // el encabezado de su grupo, la columna de tiendas y el borde de su color.
  const number = order.external_number || order.id;
  const colorClass = storeColorClass(order.store_connection);

  const item = document.createElement("div");
  item.className = order.store_connection ? `order-item from-store ${colorClass}` : "order-item";
  item.innerHTML = `
    <div class="order-item-header">
      <div class="order-item-main">
        <div class="order-item-title"><span class="order-number">#${escapeHtml(number)}</span>${order.description ? `<span class="order-desc" title="${escapeHtml(order.description)}">${escapeHtml(order.description)}</span>` : ""}</div>
        <p class="order-item-address">${order.address ? escapeHtml([order.address.recipient_name, addressSummary(order.address)].filter(Boolean).join(" · ")) : "Sin dirección"} · ${escapeHtml(formatDate(order.created_at))}</p>
      </div>
      <span class="status-badge ${escapeHtml(statusKey)}"><span class="dot"></span>${escapeHtml(statusLabel)}</span>
    </div>
    <div class="order-item-bottom">
      ${order.labels_printed_at ? `<span class="order-printed" title="Primera vez que salió su rótulo"><i class="ph ph-printer" aria-hidden="true"></i>Rótulo impreso ${escapeHtml(formatDate(order.labels_printed_at))}</span>` : ""}
      ${events.length > 1 ? `<details class="order-history"><summary>Historial (${events.length})</summary><ol>${timeline}</ol></details>` : ""}
      <div class="order-item-footer">${actions.join("")}</div>
    </div>
  `;
  window.OrderShipping.decorate(order, item);
  return item;
}

function renderOrdersPager(data) {
  ordersHasNext = Boolean(data.next);
  const pager = document.getElementById("ordersPager");
  if (!pager) return;
  pager.style.display = ordersHasNext || ordersPage > 1 ? "flex" : "none";
  document.getElementById("ordersPageInfo").textContent = `Página ${ordersPage}`;
  document.getElementById("ordersPrevBtn").disabled = ordersPage <= 1;
  document.getElementById("ordersNextBtn").disabled = !ordersHasNext;
}

async function loadOrders() {
  try {
    const params = new URLSearchParams({ page: String(ordersPage) });
    if (selectedStore) params.set("store", selectedStore);
    if (orderView) Object.entries(ORDER_VIEWS[orderView].params).forEach(([key, value]) => params.set(key, value));
    renderOrderViewNotice();
    const response = await apiFetch(`${ORDERS_URL}?${params.toString()}`);
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);
    const data = await response.json();
    const orders = extractResults(data);
    // El envío de cada pedido (Andreani) se pide antes de dibujar la lista.
    await window.OrderShipping.prepare(orders);
    renderOrderList(orders);
    renderOrdersPager(data);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar pedidos:", err);
    document.getElementById("orderList").innerHTML =
      '<p class="empty-state">No se pudieron cargar tus pedidos.</p>';
  }
}

function renderOrderViewNotice() {
  const notice = document.getElementById("orderViewNotice");
  if (!notice) return;
  notice.hidden = !orderView;
  if (!orderView) return;
  document.getElementById("orderViewLabel").textContent = ORDER_VIEWS[orderView].label;
}

function clearOrderView() {
  orderView = "";
  ordersPage = 1;
  const url = new URL(location.href);
  url.searchParams.delete("vista");
  history.replaceState(null, "", url);
  loadOrders();
}

// Filtro por tienda: un botón por cada tienda del usuario (incluye las
// desconectadas, porque sus pedidos siguen en el historial) + "Todas" +
// "Cargados a mano". Se arma con la API, así una tienda que se conecta
// aparece sola: al cargar la página y cada vez que se vuelve a esta pestaña.
// Sin tiendas, o si el usuario no puede verlas, el filtro queda oculto.
function storeColorClass(storeId) {
  if (!storeId || !storeColors.has(storeId)) return "";
  return `store-color-${storeColors.get(storeId)}`;
}

function storeChip(value, label, { colorClass = "", hint = "", muted = false, kind = "" } = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `store-chip ${colorClass}${muted ? " muted" : ""}`;
  button.dataset.store = value;
  button.title = hint ? `${label} · ${hint}` : label;
  button.setAttribute("aria-pressed", String(value === selectedStore));
  button.innerHTML = `
    ${storeMonogram(label, kind)}
    <span class="store-chip-text">
      <span class="store-chip-name">${escapeHtml(label)}</span>
      ${hint ? `<span class="store-chip-hint">${escapeHtml(hint)}</span>` : ""}
    </span>
  `;
  return button;
}

function renderStoreFilter(stores) {
  const field = document.getElementById("orderStoreFilterField");
  const group = document.getElementById("orderStoreFilter");
  if (!field || !group) return;

  const sorted = [...stores].sort((a, b) => a.id - b.id);
  storeColors = new Map(sorted.map((store, index) => [store.id, index % STORE_COLOR_COUNT]));
  storeOrder = sorted.map((store) => store.id);

  // La tienda elegida ya no está (otra cuenta, o se borró): volver a todas.
  if (selectedStore && selectedStore !== "manual" && !storeColors.has(Number(selectedStore))) {
    selectedStore = "";
  }

  group.replaceChildren(
    storeChip("", "Todas", { hint: "Todas las tiendas", kind: "all" }),
    ...sorted.map((store) =>
      storeChip(String(store.id), store.name || `Tienda ${store.external_store_id}`, {
        colorClass: storeColorClass(store.id),
        hint: store.status === "revoked" ? "desconectada" : PLATFORM_LABELS[store.platform] || store.platform_label || "",
        muted: store.status === "revoked",
      })
    ),
    storeChip("manual", "Cargados a mano", { kind: "manual" })
  );
  field.hidden = sorted.length === 0;
}

async function loadStoreFilter() {
  try {
    const response = await apiFetch(STORES_URL);
    if (!response.ok) return false;
    const stores = extractResults(await response.json());
    const signature = JSON.stringify(stores.map((store) => [store.id, store.name, store.status]));
    if (signature === storesSignature) return false;
    storesSignature = signature;
    renderStoreFilter(stores);
    return true;
  } catch (err) {
    if (err.isSessionExpired) return false;
    console.error("Error al cargar tiendas:", err);
    return false;
  }
}

document.getElementById("orderStoreFilter")?.addEventListener("click", (event) => {
  const chip = event.target.closest(".store-chip");
  if (!chip || chip.dataset.store === selectedStore) return;
  selectedStore = chip.dataset.store;
  document.querySelectorAll("#orderStoreFilter .store-chip").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.store === selectedStore));
  });
  ordersPage = 1;
  loadOrders();
});

// Volver a esta pestaña (por ejemplo después de conectar una tienda en
// tiendas.html) actualiza los botones; si cambiaron, también la lista, para
// que los colores de los pedidos coincidan.
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState !== "visible" || !hasSession()) return;
  if (await loadStoreFilter()) loadOrders();
});

// Crea la dirección nueva (si la eligió) y devuelve su id, o el de la
// guardada. Una guardada sin nombre de quien recibe no sirve: el rótulo
// saldría dirigido al propio comercio.
async function orderAddressId() {
  const selected = document.getElementById("orderAddressSelect").value;
  if (selected !== NEW_ADDRESS) {
    const address = addresses.find((item) => String(item.id) === selected);
    if (address && !address.recipient_name) {
      throw new Error("Esa dirección no tiene el nombre de quien recibe: agregalo en Mis direcciones (Editar) o cargá una nueva.");
    }
    return selected;
  }
  const payload = readAddressFields("newAddr");
  const missing = missingAddressFields(payload);
  if (missing) throw new Error(missing);
  const response = await apiFetch(ADDRESSES_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo guardar la dirección."));
  fillAddressFields("newAddr", null);
  return data.id;
}

async function createOrder() {
  const description = document.getElementById("orderDescription").value.trim();
  const button = document.getElementById("createOrderBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Creando...";

  try {
    const addressId = await orderAddressId();
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
    document.getElementById("newOrderDialog")?.close();
    showMessage("Pedido creado. Ya está en la lista.", "success");
    ordersPage = 1;
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
  const confirmed = await appConfirm("¿Cancelar este pedido?");
  if (!confirmed) return;

  try {
    const response = await apiFetch(`${ORDERS_URL}${id}/cancel/`, { method: "POST" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(getErrorMessage(data, "No se pudo cancelar el pedido."));
    }
    await loadOrders();
    showMessage("Pedido cancelado.", "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    showMessage(err.message || "No se pudo cancelar el pedido.");
  }
}

// ---------------------------------------------------------------------------
// Delegación de clicks (los items se re-crean al pintar las listas).
// ---------------------------------------------------------------------------
document.addEventListener("click", (e) => {
  const cancelBtn = e.target.closest("[data-cancel-order]");
  if (cancelBtn) {
    cancelOrder(cancelBtn.dataset.cancelOrder);
  }
});

// Nuevo pedido a mano: las direcciones se leen al abrir el diálogo (pueden
// haberse agregado en direcciones.html en otra pestaña).
async function openNewOrder() {
  const dialog = document.getElementById("newOrderDialog");
  if (!dialog) return;
  const msg = document.getElementById("orderFormMsg");
  if (msg) msg.style.display = "none";
  dialog.showModal();
  await loadAddresses();
  if (addresses.length) document.getElementById("orderAddressSelect")?.focus();
  else document.getElementById("newAddrRecipient")?.focus();
}

document.getElementById("openNewOrderBtn")?.addEventListener("click", openNewOrder);
document.getElementById("closeNewOrderBtn")?.addEventListener("click", () => document.getElementById("newOrderDialog")?.close());
document.getElementById("createOrderBtn")?.addEventListener("click", createOrder);
document.getElementById("orderAddressSelect")?.addEventListener("change", () => {
  syncNewAddressBox();
  if (document.getElementById("orderAddressSelect").value === NEW_ADDRESS) {
    document.getElementById("newAddrRecipient")?.focus();
  }
});
renderAddressFields(document.getElementById("newAddressFields"), "newAddr");
document.getElementById("clearOrderViewBtn")?.addEventListener("click", clearOrderView);
document.getElementById("ordersPrevBtn")?.addEventListener("click", () => {
  if (ordersPage > 1) {
    ordersPage -= 1;
    loadOrders();
  }
});
document.getElementById("ordersNextBtn")?.addEventListener("click", () => {
  if (ordersHasNext) {
    ordersPage += 1;
    loadOrders();
  }
});

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
      window.AppTopbar.render(await response.json());
    }
  } catch (err) {
    if (err.isSessionExpired) return;
  }
  await loadStoreFilter();
  window.OrderShipping.init({ reloadOrders: loadOrders });
  // pedidos.html#nuevo (el buscador del panel) abre el diálogo de entrada.
  if (location.hash === "#nuevo") openNewOrder();
  await loadOrders();
}

if (!hasSession()) {
  window.location.replace("index.html");
} else {
  init();
}
