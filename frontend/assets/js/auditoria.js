// ---------------------------------------------------------------------------
// REGISTROS DE AUDITORÍA (permiso audit.view).
// (Antes era una sección de gestionuser.html; ver auditoria.html. La
// tabla de "Pedidos de todos los usuarios" que vivía en esta misma sección
// ahora es pedidos_admin.html.)
// ---------------------------------------------------------------------------
const AUDIT_LOGS_URL = `${window.APP_CONFIG.API_BASE}/audit/logs/`;

let auditPage = 1;

function auditQueryString() {
  const params = new URLSearchParams();
  const search = document.getElementById("auditSearchInput")?.value.trim();
  const category = document.getElementById("auditCategoryFilter")?.value;
  const action = document.getElementById("auditActionFilter")?.value;
  const actor = document.getElementById("auditActorFilter")?.value.trim();
  const dateFrom = document.getElementById("auditDateFrom")?.value;
  const dateTo = document.getElementById("auditDateTo")?.value;
  if (search) params.set("search", search);
  if (category) params.set("category", category);
  if (action) params.set("action", action);
  if (actor) params.set("actor", actor);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  if (auditPage > 1) params.set("page", auditPage);
  const qs = params.toString();
  return qs ? `${AUDIT_LOGS_URL}?${qs}` : AUDIT_LOGS_URL;
}

function renderAuditRows(logs) {
  const body = document.getElementById("auditBody");
  if (!body) return;
  if (!logs.length) {
    body.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px;">No hay registros para mostrar.</td></tr>`;
    return;
  }
  body.innerHTML = "";
  logs.forEach((log) => {
    const tr = document.createElement("tr");
    const hasChanges = log.changes && Object.keys(log.changes).length > 0;
    tr.innerHTML = `
      <td class="cell-muted">${formatDateTime(log.created_at)}</td>
      <td title="${escapeHtml(log.actor_email || "")}">${escapeHtml(log.actor_email || "Sistema")}</td>
      <td>${escapeHtml(actionLabel(log.action))}</td>
      <td class="cell-muted" title="${escapeHtml(log.target_repr || "")}">${escapeHtml(log.target_repr || "-")}</td>
      <td><span class="badge">${escapeHtml(log.category_label || log.category)}</span></td>
      <td>${hasChanges ? `<button type="button" class="link-btn" data-audit-changes='${JSON.stringify(log.changes).replace(/'/g, "&#39;")}'>Ver cambios</button>` : "-"}</td>
    `;
    body.appendChild(tr);
  });
}

async function loadAuditLogs() {
  const body = document.getElementById("auditBody");
  if (body) body.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px;">Cargando...</td></tr>`;
  try {
    const response = await apiFetch(auditQueryString());
    if (response.status === 403) throw new Error("No tenés permisos para ver la auditoría.");
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar los registros."));
    renderAuditRows(data.results || []);
    renderSimplePager("auditPaginationControls", "auditPaginationInfo", data.pagination, "registros", (page) => {
      auditPage = page;
      loadAuditLogs();
    });
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar auditoría:", err);
    if (body) body.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px; color:red;">${escapeHtml(err.message)}</td></tr>`;
  }
}

function openAuditChangesModal(changes) {
  const modal = document.getElementById("auditChangesModal");
  const content = document.getElementById("auditChangesContent");
  if (!modal || !content) return;
  const rows = Object.entries(changes || {}).map(([field, diff]) => {
    if (diff && typeof diff === "object" && ("from" in diff || "to" in diff)) {
      return `<div><strong>${escapeHtml(field)}:</strong> ${escapeHtml(JSON.stringify(diff.from))} → ${escapeHtml(JSON.stringify(diff.to))}</div>`;
    }
    return `<div><strong>${escapeHtml(field)}:</strong> ${escapeHtml(JSON.stringify(diff))}</div>`;
  });
  content.innerHTML = rows.join("") || "<p>Sin detalle.</p>";
  modal.style.display = "flex";
}

function closeAuditChangesModal() {
  const modal = document.getElementById("auditChangesModal");
  if (modal) modal.style.display = "none";
}

document.getElementById("auditBody")?.addEventListener("click", (event) => {
  const button = event.target.closest("[data-audit-changes]");
  if (!button) return;
  try {
    openAuditChangesModal(JSON.parse(button.getAttribute("data-audit-changes")));
  } catch {
    openAuditChangesModal({});
  }
});
document.getElementById("closeAuditChangesModal")?.addEventListener("click", closeAuditChangesModal);
document.getElementById("closeAuditChangesModalBtn")?.addEventListener("click", closeAuditChangesModal);

document.getElementById("applyAuditFiltersBtn")?.addEventListener("click", () => {
  auditPage = 1;
  loadAuditLogs();
});
document.getElementById("clearAuditFiltersBtn")?.addEventListener("click", () => {
  ["auditSearchInput", "auditCategoryFilter", "auditActionFilter", "auditActorFilter", "auditDateFrom", "auditDateTo"]
    .forEach((id) => { const el = document.getElementById(id); if (el) el.value = ""; });
  auditPage = 1;
  loadAuditLogs();
});

function bootstrap() {
  if (!canUseUserPermission("audit.view")) {
    window.location.replace("dashboard.html");
    return;
  }

  // La tabla de pedidos de todos los usuarios vivía acá adentro; ahora solo
  // queda un link a su propia página (pedidos_admin.html), visible con el
  // mismo permiso que antes gateaba esa tabla.
  const adminOrdersLinkCard = document.getElementById("adminOrdersLinkCard");
  if (adminOrdersLinkCard) {
    adminOrdersLinkCard.style.display = canUseUserPermission("orders.view_all") ? "" : "none";
  }

  loadAuditActionsCatalog();
  auditPage = 1;
  loadAuditLogs();
}

bootstrap();
