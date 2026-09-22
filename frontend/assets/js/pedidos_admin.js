// ---------------------------------------------------------------------------
// PEDIDOS DE TODOS LOS USUARIOS (permiso orders.view_all).
// (Antes era parte de gestionuser.html, donde vivía dentro de la
// sección de auditoría; ver pedidos_admin.html.)
// ---------------------------------------------------------------------------
const ADMIN_ORDERS_URL = `${window.APP_CONFIG.API_BASE}/admin/orders/`;

const ORDER_STATUS_LABELS = {
  created: "Creado", preparing: "En preparación", dispatched: "Despachado",
  in_transit: "En tránsito", delivered: "Entregado", cancelled: "Cancelado",
};

let adminOrdersPage = 1;

function adminOrdersQueryString() {
  const params = new URLSearchParams();
  const search = document.getElementById("adminOrdersSearchInput")?.value.trim();
  const statusFilter = document.getElementById("adminOrdersStatusFilter")?.value;
  const dateFrom = document.getElementById("adminOrdersDateFrom")?.value;
  const dateTo = document.getElementById("adminOrdersDateTo")?.value;
  if (search) params.set("search", search);
  if (statusFilter) params.set("status", statusFilter);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  if (adminOrdersPage > 1) params.set("page", adminOrdersPage);
  const qs = params.toString();
  return qs ? `${ADMIN_ORDERS_URL}?${qs}` : ADMIN_ORDERS_URL;
}

function populateOrderStatusOptions() {
  const select = document.getElementById("adminOrdersStatusFilter");
  if (!select || select.dataset.populated) return;
  select.dataset.populated = "true";
  Object.entries(ORDER_STATUS_LABELS).forEach(([key, label]) => {
    const opt = document.createElement("option");
    opt.value = key;
    opt.textContent = label;
    select.appendChild(opt);
  });
}

function renderAdminOrdersRows(orders) {
  const body = document.getElementById("adminOrdersBody");
  if (!body) return;
  if (!orders.length) {
    body.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:20px;">No hay pedidos para mostrar.</td></tr>`;
    return;
  }
  body.innerHTML = "";
  orders.forEach((order) => {
    const tr = document.createElement("tr");
    const lastEvent = order.last_event
      ? `${order.last_event.status_label} (${formatDateTime(order.last_event.created_at)})`
      : "-";
    tr.innerHTML = `
      <td title="${escapeHtml(order.user_email || "")}">${escapeHtml(order.user_email || "-")}</td>
      <td class="cell-muted">${escapeHtml(order.description || `Pedido #${order.id}`)}</td>
      <td>${escapeHtml(order.status_label || order.status)}</td>
      <td class="cell-muted">${escapeHtml(lastEvent)}</td>
      <td class="cell-muted">${formatDateTime(order.created_at)}</td>
    `;
    body.appendChild(tr);
  });
}

async function loadAdminOrders() {
  const body = document.getElementById("adminOrdersBody");
  if (body) body.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:20px;">Cargando...</td></tr>`;
  try {
    const response = await apiFetch(adminOrdersQueryString());
    if (response.status === 403) throw new Error("No tenés permisos para ver los pedidos.");
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar los pedidos."));
    renderAdminOrdersRows(data.results || []);
    renderSimplePager("adminOrdersPaginationControls", "adminOrdersPaginationInfo", data.pagination, "pedidos", (page) => {
      adminOrdersPage = page;
      loadAdminOrders();
    });
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar pedidos:", err);
    if (body) body.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:20px; color:red;">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById("applyAdminOrdersFiltersBtn")?.addEventListener("click", () => {
  adminOrdersPage = 1;
  loadAdminOrders();
});
document.getElementById("clearAdminOrdersFiltersBtn")?.addEventListener("click", () => {
  ["adminOrdersSearchInput", "adminOrdersStatusFilter", "adminOrdersDateFrom", "adminOrdersDateTo"]
    .forEach((id) => { const el = document.getElementById(id); if (el) el.value = ""; });
  adminOrdersPage = 1;
  loadAdminOrders();
});

function bootstrap() {
  if (!canUseUserPermission("orders.view_all")) {
    window.location.replace("dashboard.html");
    return;
  }
  populateOrderStatusOptions();
  adminOrdersPage = 1;
  loadAdminOrders();
}

bootstrap();
