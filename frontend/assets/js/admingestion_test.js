const statusLabel = {
  active: "Activo",
  inactive: "Inactivo",
};
// Clave de la clase CSS del badge de rol: SIEMPRE el texto original que
// manda el backend (role_label, en inglés). No traducir estas claves —
// solo se traduce el texto que se muestra (ver roleLabelEs / translateRole).
const roleClass = {
  Admin: "admin",
  Designer: "designer",
  Operator: "operator",
  Subscriber: "subscriber",
};
const roleLabelEs = {
  Admin: "Administrador",
  Designer: "Diseñador",
  Operator: "Operador",
  Subscriber: "Suscriptor",
  User: "Usuario",
};
function translateRole(role) {
  return roleLabelEs[role] || role;
}
function formatLockoutRemaining(seconds) {
  const totalMinutes = Math.ceil((seconds || 0) / 60);
  if (totalMinutes <= 1) return "Locked - menos de 1 min restante";
  return `Locked - ${totalMinutes} min restantes`;
}
function formatLastLogin(value) {
  if (!value) return "Nunca";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Nunca";
  return date.toLocaleString("es-AR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
// Igual que formatLastLogin pero con el texto correcto para "sin pedidos".
function formatLastOrderDate(value) {
  if (!value) return "Sin pedidos";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Sin pedidos";
  return date.toLocaleString("es-AR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

const tbody = document.getElementById("usersBody");

// Endpoint real del proyecto
const API_URL = "http://127.0.0.1:8000/api/v1/users/";
const ME_URL = "http://127.0.0.1:8000/api/v1/auth/me/";
const CHANGE_PASSWORD_URL = "http://127.0.0.1:8000/api/v1/auth/me/change-password/";
const ROLES_URL = "http://127.0.0.1:8000/api/v1/auth/roles/";
const PERMISSIONS_URL = "http://127.0.0.1:8000/api/v1/auth/permissions/";
const EXPORT_CSV_URL = "http://127.0.0.1:8000/api/v1/users/export/";
const METRICS_URL = "http://127.0.0.1:8000/api/v1/users/metrics/";

// ---------------------------------------------------------------------------
// MODO DE INTERFAZ: se detecta desde el objeto user guardado en localStorage.
// El backend es la autoridad real de permisos; esto solo elige la UI.
// ---------------------------------------------------------------------------
function getCurrentUser() {
  try {
    return JSON.parse(localStorage.getItem("user") || "{}");
  } catch {
    return {};
  }
}

function isAdminMode() {
  const u = getCurrentUser();
  const role = String(u.role || "").toLowerCase();

  return Boolean(
    u.can_manage_users ||
    u.is_staff ||
    role === "admin" ||
    role === "administrador"
  );
}

function getCurrentPermissions() {
  const permissions = getCurrentUser().permissions;
  return Array.isArray(permissions) ? new Set(permissions) : new Set();
}

function canUseUserPermission(permission) {
  const currentUser = getCurrentUser();
  const permissions = getCurrentPermissions();
  // Compatibilidad de UI con sesiones antiguas: el backend continúa siendo
  // la autoridad y siempre decide la respuesta final.
  return permissions.has(permission) || (currentUser.is_staff && permissions.size === 0);
}

function canViewUsers() {
  return canUseUserPermission("users.view");
}

// ---------------------------------------------------------------------------
// apiFetch: wrapper centralizado de fetch.
// - Agrega el header Authorization con el access token.
// - Si la API responde 401 (token expirado/inválido), muestra un aviso,
//   elimina el access token y redirige a index.html para volver a iniciar sesión.
// ---------------------------------------------------------------------------
async function apiFetch(url, options = {}) {
  const headers = {
    ...(options.headers || {}),
    Authorization: `Bearer ${localStorage.getItem("access") || ""}`,
  };

  const response = await fetch(url, { ...options, headers });

  if (response.status === 401) {
    handleSessionExpired();
    const sessionError = new Error("Sesión expirada");
    sessionError.isSessionExpired = true;
    throw sessionError;
  }

  // 403 estructurado del backend (JWTAuthenticationWithPasswordPolicy):
  // la cuenta tiene un cambio de contraseña pendiente y no puede seguir
  // usando la API hasta completarlo. Se clona la respuesta para poder
  // leer el body acá sin consumirlo (el caller puede necesitar leerlo).
  if (response.status === 403) {
    const body = await response.clone().json().catch(() => ({}));
    if (body && body.must_change_password) {
      window.location.replace("cambiar-password.html");
      const pendingError = new Error("Cambio de contraseña pendiente");
      pendingError.isSessionExpired = true; // mismo tratamiento: no seguir procesando
      throw pendingError;
    }
  }

  return response;
}

// Estado de búsqueda, filtros y paginación
let currentPage = 1;
let currentSearch = "";
let currentStatus = "all";
let currentRole = "all";
// Filtros avanzados (actividad / seguridad / origen / orden).
let currentDateJoinedFrom = "";
let currentDateJoinedTo = "";
let currentLastLoginFrom = "";
let currentLastLoginTo = "";
let currentNeverLoggedIn = false;
let currentInactiveDays = "";
let currentLocked = "all";
let currentFailedAttempts = "all";
let currentAuthMethod = "all";
let currentOrderingField = "id";
let currentOrderingDir = "asc";
let lastPage = 1;
let lastUsers = [];
let editingUserId = null;
// IDs de usuarios seleccionados (checkboxes) en la vista/página actual.
let selectedUserIds = new Set();

// baseUrl parametrizable para reusar los mismos filtros activos en la
// exportación CSV (misma "lista actualmente filtrada/visible" que la tabla).
function buildQueryString(baseUrl = API_URL, { includePage = true } = {}) {
  const params = new URLSearchParams();
  if (currentSearch) params.set("search", currentSearch);
  if (currentStatus && currentStatus !== "all") params.set("status", currentStatus);
  if (currentRole && currentRole !== "all") params.set("role", currentRole);

  if (currentDateJoinedFrom) params.set("date_joined_from", currentDateJoinedFrom);
  if (currentDateJoinedTo) params.set("date_joined_to", currentDateJoinedTo);
  if (currentLastLoginFrom) params.set("last_login_from", currentLastLoginFrom);
  if (currentLastLoginTo) params.set("last_login_to", currentLastLoginTo);
  if (currentNeverLoggedIn) params.set("never_logged_in", "true");
  if (currentInactiveDays) params.set("inactive_days", currentInactiveDays);
  if (currentLocked && currentLocked !== "all") params.set("locked", currentLocked);
  if (currentFailedAttempts && currentFailedAttempts !== "all") {
    params.set("failed_attempts", currentFailedAttempts);
  }
  if (currentAuthMethod && currentAuthMethod !== "all") params.set("auth_method", currentAuthMethod);
  if (currentOrderingField && currentOrderingField !== "id") {
    const prefix = currentOrderingDir === "desc" ? "-" : "";
    params.set("ordering", `${prefix}${currentOrderingField}`);
  }

  if (includePage && currentPage > 1) params.set("page", currentPage);
  const qs = params.toString();
  return qs ? `${baseUrl}?${qs}` : baseUrl;
}

async function loadUsers() {
  tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:20px;">Cargando usuarios...</td></tr>`;

  try {
    const response = await apiFetch(buildQueryString());

    if (!response.ok) {
      // 400 = filtro inválido (fecha mal formada, rango invertido, etc.):
      // el backend manda el detalle en el body.
      if (response.status === 400) {
        const errorBody = await response.json().catch(() => null);
        const detail = errorBody && (errorBody.detail || Object.values(errorBody)[0]);
        const filterError = new Error(
          Array.isArray(detail) ? detail[0] : detail || "Filtro inválido."
        );
        filterError.isFilterValidationError = true;
        throw filterError;
      }
      throw new Error(`Error HTTP: ${response.status}`);
    }

    const raw = await response.json();

    // La API devuelve { results: [...], pagination, summary }
    const users = Array.isArray(raw) ? raw : raw.results || [];
    lastUsers = users;
    const pagination = raw.pagination || null;

    if (users.length === 0) {
      tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:20px;">No hay usuarios para mostrar.</td></tr>`;
    } else {
      renderUsers(users);
    }

    renderPagination(pagination);
    renderSummary(raw.summary);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar usuarios:", err);
    const message = err.isFilterValidationError
      ? err.message
      : "No se pudieron cargar los usuarios. Intentá de nuevo más tarde.";
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center; padding:20px; color:red;">
      ${message}
    </td></tr>`;
  }
}

function renderUsers(users) {
  tbody.innerHTML = ""; // limpia el "Cargando..."
  // Cada render (búsqueda, filtro, cambio de página) trae filas nuevas,
  // así que la selección de checkboxes se reinicia junto con ellas.
  selectedUserIds.clear();
  const currentUser = getCurrentUser();
  const currentUserId = currentUser && Number(currentUser.id);
  const canEditUsers = canUseUserPermission("users.edit");
  const canDeactivateUsers = canUseUserPermission("users.deactivate");
  const canReactivateUsers = canUseUserPermission("users.reactivate");
  const canUnlockUsers = canUseUserPermission("users.unlock");

  users.forEach((u, i) => {
    const tr = document.createElement("tr");
    const role = u.role_label || "User";
    const roleText = translateRole(role);
    const statusKey = u.status_key || (u.is_active ? "active" : "inactive");
    const statusText = statusLabel[statusKey] || u.status_label || statusKey;
    const isSelf = Number(u.id) === currentUserId;
    const lockedLine = u.is_locked
      ? `<div style="font-size:11px; color:#7c3aed; margin-top:2px;">${formatLockoutRemaining(u.lockout_remaining_seconds)}</div>`
      : "";
    // El admin NO puede desactivarse/eliminarse a sí mismo (protección doble:
    // backend devuelve 403 y el frontend no muestra siquiera la opción).
    const deleteAction = !canDeactivateUsers
      ? ""
      : isSelf
      ? `<span class="self-protected" style="font-size:12px; color:#94a3b8;" title="No podés desactivar tu propia cuenta">No podés eliminarte</span>`
      : `<a href="#" class="danger delete-user" data-delete-id="${u.id}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>Eliminar</a>`;

    tr.innerHTML = `
      <td><input type="checkbox" class="row-checkbox" data-user-id="${u.id}"></td>
      <td>
        <div class="user-cell">
          <img class="user-avatar" src="https://api.dicebear.com/7.x/avataaars/svg?seed=${u.email || u.id}" alt="">
          <span class="user-name" title="${u.display_name || u.email}">${u.display_name || u.email}</span>
        </div>
      </td>
      <td class="cell-muted" title="${u.email || ""}">${u.email}</td>
      <td><span class="badge ${roleClass[role] || ""}">${roleText}</span></td>
      <td class="status-cell"><span class="status ${statusKey}"><span class="dot"></span>${statusText}</span>${lockedLine}</td>
      <td class="cell-muted">${u.created_date || "-"}</td>
      <td class="cell-muted">${u.orders_count ?? 0}</td>
      <td class="cell-muted">${formatLastOrderDate(u.last_order_at)}</td>
      <td class="actions-col">
        <div class="row-actions">
          <button class="more-btn" data-idx="${i}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg>
          </button>
          <div class="dropdown" id="dd-${i}" style="display:none;">
            <a href="#" class="view-user" data-view-id="${u.id}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>Ver</a>
            ${canEditUsers ? `<a href="#" class="edit-user" data-edit-id="${u.id}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/></svg>Editar</a>` : ""}
            <div class="sep"></div>
            ${canReactivateUsers && u.can_reactivate ? `<a href="#" class="reactivate-user" data-reactivate-id="${u.id}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/><path d="M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>Reactivar</a>` : ""}
            ${canUnlockUsers && u.is_locked ? `<a href="#" class="unlock-user" data-unlock-id="${u.id}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/></svg>Unlock</a>` : ""}
            ${deleteAction}
          </div>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });

  attachDropdownListeners();
  updateSelectAllCheckboxState();
  updateBulkActionsBar();
}

// ---------------------------------------------------------------------------
// SELECCIÓN DE USUARIOS: checkbox del header (seleccionar todos) + checkboxes
// por fila. Se reinicia en cada renderUsers() porque las filas se recrean.
// ---------------------------------------------------------------------------
function updateSelectAllCheckboxState() {
  const selectAllCheckbox = document.getElementById("selectAllUsers");
  if (!selectAllCheckbox) return;
  const rowCheckboxes = document.querySelectorAll(".row-checkbox");
  const total = rowCheckboxes.length;
  const checkedCount = document.querySelectorAll(".row-checkbox:checked").length;
  selectAllCheckbox.checked = total > 0 && checkedCount === total;
  selectAllCheckbox.indeterminate = checkedCount > 0 && checkedCount < total;
}

// ---------------------------------------------------------------------------
// ACCIONES MASIVAS: barra que aparece cuando hay usuarios tildados. Reusa
// selectedUserIds (ya mantenido por el listener de checkboxes de arriba).
// ---------------------------------------------------------------------------
function updateBulkActionsBar() {
  const bar = document.getElementById("bulkActionsBar");
  const countEl = document.getElementById("bulkActionsCount");
  if (!bar) return;
  const count = selectedUserIds.size;
  bar.style.display = count > 0 ? "flex" : "none";
  if (countEl) countEl.textContent = `${count} seleccionado${count === 1 ? "" : "s"}`;
}

async function runBulkAction(payload) {
  if (selectedUserIds.size === 0) {
    alert("Seleccioná al menos un usuario (tildá su casilla).");
    return;
  }
  try {
    const response = await apiFetch(`${API_URL}bulk-actions/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: Array.from(selectedUserIds), ...payload }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data && (data.detail || Object.values(data)[0]);
      throw new Error(Array.isArray(detail) ? detail[0] : detail || `Error HTTP: ${response.status}`);
    }

    const skippedCount = (data.skipped || []).length;
    let message = `Acción aplicada a ${data.updated.length} usuario(s).`;
    if (skippedCount > 0) {
      const reasons = data.skipped.map((s) => `#${s.id}: ${s.reason}`).join("\n");
      message += `\n${skippedCount} se salteó(aron):\n${reasons}`;
    }
    alert(message);
    loadUsers();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error en la acción masiva:", err);
    alert(err.message || "No se pudo completar la acción masiva.");
  }
}

document.getElementById("bulkActivateBtn")?.addEventListener("click", () => {
  runBulkAction({ action: "activate" });
});
document.getElementById("bulkDeactivateBtn")?.addEventListener("click", () => {
  runBulkAction({ action: "deactivate" });
});
document.getElementById("bulkSetRoleBtn")?.addEventListener("click", () => {
  const role = document.getElementById("bulkRoleSelect")?.value;
  if (!role) {
    alert("Elegí un rol para aplicar.");
    return;
  }
  runBulkAction({ action: "set_role", role });
});

document.addEventListener("change", (e) => {
  const rowCheckbox = e.target.closest(".row-checkbox");
  if (rowCheckbox) {
    const userId = parseInt(rowCheckbox.dataset.userId, 10);
    if (rowCheckbox.checked) {
      selectedUserIds.add(userId);
    } else {
      selectedUserIds.delete(userId);
    }
    const row = rowCheckbox.closest("tr");
    if (row) row.classList.toggle("row-selected", rowCheckbox.checked);
    updateSelectAllCheckboxState();
    updateBulkActionsBar();
    return;
  }

  const selectAllCheckbox = e.target.closest("#selectAllUsers");
  if (selectAllCheckbox) {
    const checked = selectAllCheckbox.checked;
    document.querySelectorAll(".row-checkbox").forEach((cb) => {
      cb.checked = checked;
      const userId = parseInt(cb.dataset.userId, 10);
      if (checked) {
        selectedUserIds.add(userId);
      } else {
        selectedUserIds.delete(userId);
      }
      const row = cb.closest("tr");
      if (row) row.classList.toggle("row-selected", checked);
    });
    selectAllCheckbox.indeterminate = false;
    updateBulkActionsBar();
  }
});

// ---------------------------------------------------------------------------
// EXPORTAR A EXCEL: toma los usuarios tildados (selectedUserIds) de la
// página/filtro actual y arma un .xlsx en el navegador con SheetJS (no pega
// contra el backend: los datos ya están en lastUsers, cargados por loadUsers).
// ---------------------------------------------------------------------------
const exportUsersBtn = document.getElementById("exportUsersBtn");
if (exportUsersBtn) {
  exportUsersBtn.addEventListener("click", () => {
    if (selectedUserIds.size === 0) {
      alert("Seleccioná al menos un usuario (tildá su casilla) para exportar.");
      return;
    }

    if (typeof XLSX === "undefined") {
      alert("No se pudo cargar la librería de Excel. Revisá tu conexión e intentá de nuevo.");
      return;
    }

    const selectedUsers = lastUsers.filter((u) => selectedUserIds.has(Number(u.id)));
    const rows = selectedUsers.map((u) => ({
      ID: u.id,
      Nombre: u.display_name || "",
      Email: u.email || "",
      Rol: translateRole(u.role_label || "User"),
      Estado: statusLabel[u.status_key] || u.status_label || (u.is_active ? "Activo" : "Inactivo"),
      "Fecha de creación": u.created_date || "",
    }));

    const worksheet = XLSX.utils.json_to_sheet(rows);
    worksheet["!cols"] = [
      { wch: 6 },
      { wch: 28 },
      { wch: 32 },
      { wch: 14 },
      { wch: 12 },
      { wch: 16 },
    ];

    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, worksheet, "Usuarios");

    const today = new Date().toISOString().slice(0, 10);
    XLSX.writeFile(workbook, `usuarios-${today}.xlsx`);
  });
}

// ---------------------------------------------------------------------------
// EXPORTAR A CSV: generado en el BACKEND (a diferencia del .xlsx de arriba,
// que arma el navegador). Si hay selección, exporta solo esos IDs; si no,
// exporta el resultado de los filtros actualmente activos (sin límite de
// página: toda la lista filtrada, no solo la página visible).
// ---------------------------------------------------------------------------
const exportUsersCsvBtn = document.getElementById("exportUsersCsvBtn");
if (exportUsersCsvBtn) {
  exportUsersCsvBtn.addEventListener("click", async () => {
    let url = buildQueryString(EXPORT_CSV_URL, { includePage: false });
    if (selectedUserIds.size > 0) {
      const separator = url.includes("?") ? "&" : "?";
      url += `${separator}ids=${Array.from(selectedUserIds).join(",")}`;
    }

    try {
      const response = await apiFetch(url);
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `Error HTTP: ${response.status}`);
      }
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = `usuarios-${new Date().toISOString().slice(0, 10)}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (err) {
      if (err.isSessionExpired) return;
      console.error("Error al exportar CSV:", err);
      alert(err.message || "No se pudo exportar el CSV.");
    }
  });
}

function renderPagination(pagination) {
  const infoEl = document.getElementById("paginationInfo");
  const numbersEl = document.getElementById("pageNumbers");
  const controlsEl = document.getElementById("paginationControls");

  if (!pagination) {
    if (infoEl) infoEl.textContent = "Mostrando 0 usuarios";
    if (numbersEl) numbersEl.innerHTML = "";
    return;
  }

  const { count, from, to, total_pages, page, has_previous, has_next, previous_page, next_page } = pagination;
  lastPage = total_pages || 1;

  if (infoEl) {
    infoEl.textContent = count > 0
      ? `Mostrando ${from} a ${to} de ${count} usuarios`
      : "Mostrando 0 usuarios";
  }

  // Botones prev/next/first/last
  if (controlsEl) {
    const firstBtn = controlsEl.querySelector('[data-page="first"]');
    const prevBtn = controlsEl.querySelector('[data-page="prev"]');
    const nextBtn = controlsEl.querySelector('[data-page="next"]');
    const lastBtn = controlsEl.querySelector('[data-page="last"]');

    if (firstBtn) firstBtn.disabled = !has_previous;
    if (prevBtn) prevBtn.disabled = !has_previous;
    if (nextBtn) nextBtn.disabled = !has_next;
    if (lastBtn) {
      lastBtn.disabled = !has_next;
      lastBtn.dataset.lastPage = lastPage;
    }
  }

  // Números de página
  if (numbersEl) {
    const pages = pagination.pages || [];
    numbersEl.innerHTML = pages.map((p) => {
      if (p.type === "ellipsis") {
        return `<span class="dots">…</span>`;
      }
      return `<button class="${p.active ? "active" : ""}" data-page-number="${p.number}">${p.number}</button>`;
    }).join("");
  }
}

function renderSummary(summary) {
  if (!summary) return;
  const totalEl = document.getElementById("totalUsersCount");
  const activeEl = document.getElementById("activeUsersCount");
  const inactiveEl = document.getElementById("inactiveUsersCount");
  const lockedEl = document.getElementById("lockedUsersCount");
  if (totalEl) totalEl.textContent = summary.total ?? "—";
  if (activeEl) activeEl.textContent = summary.active ?? "—";
  if (inactiveEl) inactiveEl.textContent = summary.inactive ?? "—";
  if (lockedEl) lockedEl.textContent = summary.locked ?? "—";
}

function attachDropdownListeners() {
  document.querySelectorAll(".more-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const idx = btn.dataset.idx;
      document.querySelectorAll(".dropdown").forEach((dd) => {
        if (dd.id !== `dd-${idx}`) dd.style.display = "none";
      });
      const dd = document.getElementById(`dd-${idx}`);
      dd.style.display = dd.style.display === "block" ? "none" : "block";
    });
  });
}

// Delegación: los links del dropdown se re-crean al pintar la tabla,
// así que escuchamos clicks desde el contenedor raíz.
document.addEventListener("click", (e) => {
  const editLink = e.target.closest(".edit-user");
  if (editLink) {
    e.preventDefault();
    const userId = parseInt(editLink.dataset.editId, 10);
    if (userId) openEditPanel(userId);
    return;
  }

  const viewLink = e.target.closest(".view-user");
  if (viewLink) {
    e.preventDefault();
    const userId = parseInt(viewLink.dataset.viewId, 10);
    if (userId) openViewModal(userId);
    return;
  }

  const deleteLink = e.target.closest(".delete-user");
  if (deleteLink) {
    e.preventDefault();
    const userId = parseInt(deleteLink.dataset.deleteId, 10);
    if (userId) softDeleteUser(userId);
    return;
  }

  const reactivateLink = e.target.closest(".reactivate-user");
  if (reactivateLink) {
    e.preventDefault();
    const userId = parseInt(reactivateLink.dataset.reactivateId, 10);
    if (userId) reactivateUser(userId);
    return;
  }

  const unlockLink = e.target.closest(".unlock-user");
  if (unlockLink) {
    e.preventDefault();
    const userId = parseInt(unlockLink.dataset.unlockId, 10);
    if (userId) unlockUser(userId);
  }
});

function reactivateUser(userId) {
  const user = lastUsers.find((u) => Number(u.id) === userId);
  const userName = user ? user.display_name || user.email : `#${userId}`;

  const confirmed = window.confirm(
    `¿Reactivar al usuario "${userName}"?\n\nEl usuario volverá a estar activo.`
  );
  if (!confirmed) return;

  apiFetch(`${API_URL}${userId}/`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ status: "active" }),
  })
    .then(async (response) => {
      if (response.status === 403) {
        throw new Error("No tenés permisos para reactivar usuarios.");
      }
      if (response.status === 404) {
        throw new Error("El usuario no existe.");
      }
      if (!response.ok) {
        throw new Error(`Error HTTP: ${response.status}`);
      }
    })
    .then(() => {
      alert(`El usuario "${userName}" fue reactivado correctamente.`);
      // Recargar la lista manteniendo la página actual si sigue siendo válida.
      loadUsers();
    })
    .catch((err) => {
      if (err.isSessionExpired) return;
      console.error("Error al reactivar usuario:", err);
      alert(err.message || "No se pudo reactivar el usuario. Intentá de nuevo.");
    });
}

function unlockUser(userId) {
  const user = lastUsers.find((u) => Number(u.id) === userId);
  const userName = user ? user.display_name || user.email : `#${userId}`;

  const confirmed = window.confirm(
    `¿Desbloquear al usuario "${userName}"?\n\nSe reseteará el bloqueo y el contador de intentos fallidos.`
  );
  if (!confirmed) return;

  apiFetch(`${API_URL}${userId}/unlock/`, {
    method: "POST",
  })
    .then(async (response) => {
      if (response.status === 403) {
        throw new Error("No tenés permisos para desbloquear usuarios.");
      }
      if (response.status === 404) {
        throw new Error("El usuario no existe.");
      }
      if (!response.ok) {
        throw new Error(`Error HTTP: ${response.status}`);
      }
    })
    .then(() => {
      alert(`El usuario "${userName}" fue desbloqueado correctamente.`);
      loadUsers();
    })
    .catch((err) => {
      if (err.isSessionExpired) return;
      console.error("Error al desbloquear usuario:", err);
      alert(err.message || "No se pudo desbloquear el usuario. Intentá de nuevo.");
    });
}

function softDeleteUser(userId) {
  const user = lastUsers.find((u) => Number(u.id) === userId);
  const userName = user ? user.display_name || user.email : `#${userId}`;

  const confirmed = window.confirm(
    `¿Desactivar al usuario "${userName}"?\n\nEl usuario será desactivado (soft delete), NO eliminado permanentemente.`
  );
  if (!confirmed) return;

  apiFetch(`${API_URL}${userId}/`, {
    method: "DELETE",
  })
    .then(async (response) => {
      if (response.status === 403) {
        throw new Error("No tenés permisos para desactivar usuarios.");
      }
      if (response.status === 404) {
        throw new Error("El usuario no existe.");
      }
      if (!response.ok) {
        throw new Error(`Error HTTP: ${response.status}`);
      }
    })
    .then(() => {
      alert(`El usuario "${userName}" fue desactivado correctamente.`);
      // Recargar la lista manteniendo la página actual si sigue siendo válida.
      // Si la página actual quedó fuera de rango, el backend ajusta automáticamente.
      loadUsers();
    })
    .catch((err) => {
      if (err.isSessionExpired) return;
      console.error("Error al desactivar usuario:", err);
      alert(err.message || "No se pudo desactivar el usuario. Intentá de nuevo.");
    });
}

function closeViewModal() {
  const modal = document.getElementById("viewUserModal");
  if (modal) modal.style.display = "none";
}

function openViewModal(userId) {
  const modal = document.getElementById("viewUserModal");
  const content = document.getElementById("viewUserContent");
  if (!modal || !content) return;

  content.innerHTML = `<div style="text-align:center; color:#64748b; padding:20px;">Cargando usuario...</div>`;
  modal.style.display = "flex";

  apiFetch(`${API_URL}${userId}/`)
    .then(async (response) => {
      if (response.status === 403) {
        throw new Error("No tenés permisos para ver este usuario.");
      }
      if (response.status === 404) {
        throw new Error("El usuario no existe.");
      }
      if (!response.ok) {
        throw new Error(`Error HTTP: ${response.status}`);
      }
      return response.json();
    })
    .then((user) => {
      const role = translateRole(user.role_label || "User");
      const statusKey = user.status_key || (user.is_active ? "active" : "inactive");
      const statusText = statusLabel[statusKey] || user.status_label || statusKey;

      const lockText = user.is_locked
        ? formatLockoutRemaining(user.lockout_remaining_seconds)
        : "No";

      const rows = [
        ["ID", user.id ?? "-"],
        ["Nombre", user.display_name || "-"],
        ["Email", user.email || "-"],
        ["Rol", role],
        ["Estado", statusText],
        ["Bloqueo", lockText],
        ["Creado", user.created_date || "-"],
        ["Último acceso", formatLastLogin(user.last_login)],
        ["Pedidos totales", user.orders_count ?? 0],
        ["Último pedido", formatLastOrderDate(user.last_order_at)],
        ["Cambio de contraseña pendiente", user.must_change_password ? "Sí" : "No"],
      ];

      content.innerHTML = rows
        .map(
          ([label, value]) => `
            <div style="display:flex; justify-content:space-between; align-items:baseline; gap:16px; padding:9px 0; border-bottom:1px solid #e7eaf1;">
              <span style="flex:0 0 auto; max-width:55%; font-size:12.5px; font-weight:600; color:#64748b; line-height:1.35;">${label}</span>
              <span style="flex:1; font-size:14px; color:#0f172a; text-align:right; word-break:break-word;">${value}</span>
            </div>
          `
        )
        .join("");
    })
    .catch((err) => {
      if (err.isSessionExpired) return;
      console.error("Error al ver usuario:", err);
      content.innerHTML = `<div style="text-align:center; color:#dc2626; padding:20px;">${err.message}</div>`;
    });
}

// ---------------------------------------------------------------------------
// ROLES DINÁMICOS: carga TODOS los Django Groups (básicos + personalizados)
// en el <select> de crear/editar usuario y en el filtro de la tabla.
// ---------------------------------------------------------------------------
async function loadRoleOptions() {
  const roleSelect = document.getElementById("roleSelect");
  const roleFilter = document.getElementById("userRoleFilter");
  const bulkRoleSelect = document.getElementById("bulkRoleSelect");
  if (!roleSelect && !roleFilter && !bulkRoleSelect) return;

  try {
    const response = await apiFetch(ROLES_URL);
    if (!response.ok) return;
    const data = await response.json();
    const roles = Array.isArray(data) ? data : data.results || data.roles || [];

    const options = roles.map((role) => {
      const key = String(role.key || role.name).toLowerCase();
      const label = role.label || key.charAt(0).toUpperCase() + key.slice(1);
      return { key, label };
    });

    if (roleSelect) {
      const current = roleSelect.value;
      roleSelect.innerHTML = '<option value="">Seleccioná un rol</option>';
      options.forEach(({ key, label }) => {
        const opt = document.createElement("option");
        opt.value = key;
        opt.textContent = label;
        roleSelect.appendChild(opt);
      });
      if (current) roleSelect.value = current;
    }

    if (roleFilter) {
      const current = roleFilter.value;
      roleFilter.innerHTML = '<option value="all">Todos</option>';
      options.forEach(({ key, label }) => {
        const opt = document.createElement("option");
        opt.value = key;
        opt.textContent = label;
        roleFilter.appendChild(opt);
      });
      if (current) roleFilter.value = current;
    }

    if (bulkRoleSelect) {
      bulkRoleSelect.innerHTML = '<option value="">Cambiar rol a...</option>';
      options.forEach(({ key, label }) => {
        const opt = document.createElement("option");
        opt.value = key;
        opt.textContent = label;
        bulkRoleSelect.appendChild(opt);
      });
    }
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar roles:", err);
  }
}

function resetCreateForm() {
  editingUserId = null;
  const form = document.getElementById("createUserForm");
  if (form) form.reset();
  const title = document.getElementById("userFormTitle");
  const submitBtn = document.getElementById("userFormSubmit");
  if (title) title.textContent = "Crear usuario";
  if (submitBtn) submitBtn.textContent = "Crear usuario";

  // En modo creación el campo Password está visible; es opcional: si se deja
  // vacío, el backend usa una contraseña temporal (ADMIN_CREATED_USER_PASSWORD)
  // y marca la cuenta para cambiarla en el próximo login.
  const passwordField = document.getElementById("passwordField");
  const passwordInput = document.getElementById("passwordInput");
  if (passwordField) passwordField.style.display = "block";
  if (passwordInput) {
    passwordInput.value = "";
    passwordInput.required = false;
  }
  const forceChangeInput = document.getElementById("forcePasswordChangeInput");
  if (forceChangeInput) forceChangeInput.checked = false;
}

function openEditPanel(userId) {
  const user = lastUsers.find((u) => Number(u.id) === userId);
  if (!user) {
    alert("No se pudo encontrar el usuario seleccionado.");
    return;
  }

  editingUserId = userId;

  // Precargar el formulario con los datos actuales
  const fullNameInput = document.getElementById("fullNameInput");
  const emailInput = document.getElementById("emailInput");
  const roleSelect = document.getElementById("roleSelect");
  const statusSelect = document.getElementById("statusSelect");

  if (fullNameInput) fullNameInput.value = user.display_name || "";
  if (emailInput) emailInput.value = user.email || "";

  // El backend devuelve role_key / status_key en minúsculas, y los <option>
  // de rol y estado usan esas mismas claves en minúscula como value="...",
  // así que no hace falta transformar nada para matchear la option.
  const roleKey = user.role_key || "subscriber";
  const statusKey = user.status_key || (user.is_active ? "active" : "inactive");

  if (roleSelect) {
    // Los valores del <select> son los nombres de Group en minúsculas
    // (cargados dinámicamente desde /api/v1/auth/roles/).
    roleSelect.value = roleKey;
  }
  if (statusSelect) {
    // Los <option> de status ahora tienen value="active"/"inactive" explícito
    // (ya no dependen del texto visible), así que matchea directo con la key.
    statusSelect.value = statusKey;
  }

  // Cambios visuales mínimos para indicar modo edición
  const title = document.getElementById("userFormTitle");
  const submitBtn = document.getElementById("userFormSubmit");
  if (title) title.textContent = "Editar usuario";
  if (submitBtn) submitBtn.textContent = "Guardar cambios";

  // En modo edición NUNCA se muestra ni se precarga la contraseña existente.
  const passwordField = document.getElementById("passwordField");
  const passwordInput = document.getElementById("passwordInput");
  if (passwordField) passwordField.style.display = "none";
  if (passwordInput) {
    passwordInput.value = "";
    passwordInput.required = false;
  }

  // Precargar el estado actual del flag "forzar cambio de contraseña".
  const forceChangeInput = document.getElementById("forcePasswordChangeInput");
  if (forceChangeInput) forceChangeInput.checked = Boolean(user.must_change_password);

  panel.style.display = "block";
}

document.addEventListener("click", () => {
  document
    .querySelectorAll(".dropdown")
    .forEach((dd) => (dd.style.display = "none"));
});

// Búsqueda: la hace el backend vía ?search=
// Hay dos inputs con data-user-search (topbar y tabla); ambos aplican el mismo filtro.
const searchInputs = document.querySelectorAll("[data-user-search]");
let debounceTimer;
searchInputs.forEach((searchInput) => {
  if (!searchInput) return;
  searchInput.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      currentSearch = searchInput.value.trim();
      currentPage = 1; // volver a la primera página
      loadUsers();
    }, 300);
  });
});

// Filtros: los procesa el backend vía ?status= y ?role=
const openFiltersBtn = document.getElementById("openUserFilters");
const filterPanel = document.getElementById("userFilterPanel");
const statusFilter = document.getElementById("userStatusFilter");
const roleFilter = document.getElementById("userRoleFilter");

if (openFiltersBtn && filterPanel) {
  openFiltersBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    filterPanel.style.display = filterPanel.style.display === "block" ? "none" : "block";
  });
}

if (statusFilter) {
  statusFilter.addEventListener("change", () => {
    currentStatus = statusFilter.value;
    currentPage = 1;
    loadUsers();
  });
}

if (roleFilter) {
  roleFilter.addEventListener("change", () => {
    currentRole = roleFilter.value;
    currentPage = 1;
    loadUsers();
  });
}

// Filtros avanzados: actividad (fechas / inactividad), seguridad (locked /
// intentos fallidos), origen (auth_method) y orden — mismo mecanismo que
// status/role: cada control actualiza su variable current* y recarga en la
// página 1. Todos los query params los procesa el backend (ver viewsets.py).
const dateJoinedFromFilter = document.getElementById("userDateJoinedFrom");
const dateJoinedToFilter = document.getElementById("userDateJoinedTo");
const lastLoginFromFilter = document.getElementById("userLastLoginFrom");
const lastLoginToFilter = document.getElementById("userLastLoginTo");
const neverLoggedInFilter = document.getElementById("userNeverLoggedIn");
const inactiveDaysFilter = document.getElementById("userInactiveDays");
const lockedFilter = document.getElementById("userLockedFilter");
const failedAttemptsFilter = document.getElementById("userFailedAttemptsFilter");
const authMethodFilter = document.getElementById("userAuthMethodFilter");
const orderingFieldFilter = document.getElementById("userOrderingField");
const orderingDirFilter = document.getElementById("userOrderingDir");
const clearUserFiltersBtn = document.getElementById("clearUserFiltersBtn");

function bindAdvancedFilter(el, apply) {
  if (!el) return;
  el.addEventListener("change", () => {
    apply();
    currentPage = 1;
    loadUsers();
  });
}

bindAdvancedFilter(dateJoinedFromFilter, () => (currentDateJoinedFrom = dateJoinedFromFilter.value));
bindAdvancedFilter(dateJoinedToFilter, () => (currentDateJoinedTo = dateJoinedToFilter.value));
bindAdvancedFilter(lastLoginFromFilter, () => (currentLastLoginFrom = lastLoginFromFilter.value));
bindAdvancedFilter(lastLoginToFilter, () => (currentLastLoginTo = lastLoginToFilter.value));
bindAdvancedFilter(neverLoggedInFilter, () => (currentNeverLoggedIn = neverLoggedInFilter.checked));
bindAdvancedFilter(inactiveDaysFilter, () => (currentInactiveDays = inactiveDaysFilter.value.trim()));
bindAdvancedFilter(lockedFilter, () => (currentLocked = lockedFilter.value));
bindAdvancedFilter(failedAttemptsFilter, () => (currentFailedAttempts = failedAttemptsFilter.value));
bindAdvancedFilter(authMethodFilter, () => (currentAuthMethod = authMethodFilter.value));
bindAdvancedFilter(orderingFieldFilter, () => (currentOrderingField = orderingFieldFilter.value));
bindAdvancedFilter(orderingDirFilter, () => (currentOrderingDir = orderingDirFilter.value));

if (clearUserFiltersBtn) {
  clearUserFiltersBtn.addEventListener("click", () => {
    currentDateJoinedFrom = "";
    currentDateJoinedTo = "";
    currentLastLoginFrom = "";
    currentLastLoginTo = "";
    currentNeverLoggedIn = false;
    currentInactiveDays = "";
    currentLocked = "all";
    currentFailedAttempts = "all";
    currentAuthMethod = "all";
    currentOrderingField = "id";
    currentOrderingDir = "asc";

    if (dateJoinedFromFilter) dateJoinedFromFilter.value = "";
    if (dateJoinedToFilter) dateJoinedToFilter.value = "";
    if (lastLoginFromFilter) lastLoginFromFilter.value = "";
    if (lastLoginToFilter) lastLoginToFilter.value = "";
    if (neverLoggedInFilter) neverLoggedInFilter.checked = false;
    if (inactiveDaysFilter) inactiveDaysFilter.value = "";
    if (lockedFilter) lockedFilter.value = "all";
    if (failedAttemptsFilter) failedAttemptsFilter.value = "all";
    if (authMethodFilter) authMethodFilter.value = "all";
    if (orderingFieldFilter) orderingFieldFilter.value = "id";
    if (orderingDirFilter) orderingDirFilter.value = "asc";

    currentPage = 1;
    loadUsers();
  });
}

// Paginación: solicita la página correspondiente a la API
const paginationControls = document.getElementById("paginationControls");
if (paginationControls) {
  paginationControls.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn) return;

    const pageAction = btn.dataset.page;
    const pageNumber = btn.dataset.pageNumber;

    if (pageAction === "first") currentPage = 1;
    else if (pageAction === "prev") currentPage = Math.max(1, currentPage - 1);
    else if (pageAction === "next") currentPage = currentPage + 1;
    else if (pageAction === "last") {
      // Se resuelve con el total de páginas de la última respuesta
      const lastPage = btn.dataset.lastPage;
      if (lastPage) currentPage = parseInt(lastPage, 10);
    } else if (pageNumber) {
      currentPage = parseInt(pageNumber, 10);
    } else {
      return;
    }

    loadUsers();
  });
}

// Cerrar modal de View (X y botón Close)
const closeViewModalBtn = document.getElementById("closeViewModal");
const closeViewModalBtn2 = document.getElementById("closeViewModalBtn");
if (closeViewModalBtn) closeViewModalBtn.addEventListener("click", closeViewModal);
if (closeViewModalBtn2) closeViewModalBtn2.addEventListener("click", closeViewModal);

// panel de crear usuario (queda igual)
const panel = document.getElementById("createPanel");
document
  .getElementById("openCreatePanel")
  .addEventListener("click", () => {
    resetCreateForm();
    panel.style.display = "block";
  });
document
  .getElementById("closeCreatePanel")
  .addEventListener("click", () => {
    resetCreateForm();
    panel.style.display = "none";
  });
document
  .getElementById("cancelCreate")
  .addEventListener("click", () => {
    resetCreateForm();
    panel.style.display = "none";
  });

// Crear/Editar usuario: el submit se maneja más abajo (handleAdminSubmit).
const createUserForm = document.getElementById("createUserForm");

// 🚀 Logout: invalida el refresh token en el backend, limpia tokens y redirige.
const logoutBtn = document.getElementById("logoutAdminBtn");
if (logoutBtn) {
  logoutBtn.addEventListener("click", async () => {
    const refresh = localStorage.getItem("refresh") || "";

    // 1. Intentar invalidar el refresh token en el backend.
    //    Si falla (token expirado, red, etc.) igual se continúa con el logout local.
    if (refresh) {
      try {
        await apiFetch("http://127.0.0.1:8000/api/v1/auth/logout/", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ refresh }),
        });
      } catch (err) {
        console.warn("No se pudo invalidar el refresh token en el backend:", err);
      }
    }

    // 2. Limpiar tokens locales SIEMPRE (aunque el backend falle).
    localStorage.removeItem("access");
    localStorage.removeItem("refresh");
    localStorage.removeItem("user");

    // 3. El logout ya se hizo acá mismo (token invalidado + storage limpio);
    //    redirigir directo al login en vez de pasar por logoutpage.html.
    window.location.replace("index.html");
  });
}

// ---------------------------------------------------------------------------
// SETTINGS / PERFIL PROPIO (vista dentro de <main>)
// ---------------------------------------------------------------------------
function handleSessionExpired() {
  alert("Tu sesión expiró. Volvé a iniciar sesión.");
  localStorage.removeItem("access");
  localStorage.removeItem("refresh");
  localStorage.removeItem("user");
  window.location.replace("index.html");
}

async function openSettings() {
  closeRoles();
  closeReports();
  // Marcar nav item activo
  document.querySelectorAll(".nav a").forEach((a) => a.classList.remove("active"));
  const navSettings = document.getElementById("navSettings");
  if (navSettings) navSettings.classList.add("active");

  // Ocultar vistas de la tabla / mostrar settings
  const toHide = [".topbar", ".stats", ".table-card"];
  toHide.forEach((sel) => {
    const el = document.querySelector(sel);
    if (el) el.style.display = "none";
  });
  const settingsSection = document.getElementById("settingsSection");
  if (settingsSection) settingsSection.style.display = "";

  // Limpiar mensajes anteriores
  const profileMsg  = document.getElementById("settingsProfileMsg");
  const passwordMsg = document.getElementById("settingsPasswordMsg");
  if (profileMsg)  { profileMsg.style.display  = "none"; profileMsg.textContent  = ""; }
  if (passwordMsg) { passwordMsg.style.display = "none"; passwordMsg.textContent = ""; }

  // Limpiar campos de contraseña
  ["settingsCurrentPassword", "settingsNewPassword", "settingsConfirmPassword"]
    .forEach((id) => { const el = document.getElementById(id); if (el) el.value = ""; });

  // Cargar datos del perfil desde el backend
  const token = localStorage.getItem("access");
  try {
    const res = await fetch(ME_URL, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    if (res.status === 401) { handleSessionExpired(); return; }
    if (!res.ok) throw new Error("Error al cargar el perfil");
    const user = await res.json();

    const v = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.value = val ?? "";
    };
    v("settingsFirstName", user.first_name);
    v("settingsLastName",  user.last_name);
    v("settingsEmail",     user.email);
    v("settingsRole",      user.role ?? user.groups?.[0] ?? "—");
  } catch (err) {
    if (profileMsg) {
      profileMsg.textContent = "No se pudo cargar el perfil.";
      profileMsg.style.color = "red";
      profileMsg.style.display = "block";
    }
  }
}

function closeSettings() {
  const settingsSection = document.getElementById("settingsSection");
  if (settingsSection) settingsSection.style.display = "none";

  const toShow = [".topbar", ".stats", ".table-card"];

  // La tabla de usuarios se restaura si el usuario tiene el permiso
  // efectivo users.view (no depende de ser admin). Si no lo tiene,
  // solo se muestra el topbar reducido; la tabla y stats siguen ocultas.
  if (canViewUsers()) {
    toShow.forEach((sel) => {
      const el = document.querySelector(sel);
      if (el) el.style.display = "";
    });
  } else {
    const topbar = document.querySelector(".topbar");
    if (topbar) topbar.style.display = "";
  }

  // Restaurar nav item activo
  document.querySelectorAll(".nav a").forEach((a) => a.classList.remove("active"));
  const navUsers = document.querySelector(".nav a.active") ??
                   document.querySelectorAll(".nav li")[1]?.querySelector("a");
  if (navUsers) navUsers.classList.add("active");
}

async function settingsSaveProfile() {
  const firstName = document.getElementById("settingsFirstName")?.value?.trim();
  const lastName  = document.getElementById("settingsLastName")?.value?.trim();
  const msg       = document.getElementById("settingsProfileMsg");

  const showMsg = (text, ok) => {
    if (!msg) return;
    msg.textContent    = text;
    msg.style.color    = ok ? "green" : "red";
    msg.style.display  = "block";
  };

  const token = localStorage.getItem("access");
  try {
    const res = await fetch(ME_URL, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`
      },
      body: JSON.stringify({ first_name: firstName, last_name: lastName })
    });
    if (res.status === 401) { handleSessionExpired(); return; }
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      showMsg("Changes saved successfully.", true);
      // Actualizar nombre en sidebar
      const nameEl = document.getElementById("profileName");
      if (nameEl) nameEl.textContent = data.display_name ?? `${firstName} ${lastName}`.trim();
    } else {
      showMsg(data?.detail ?? data?.first_name?.[0] ?? data?.last_name?.[0] ?? "Error saving changes.", false);
    }
  } catch {
    showMsg("Network error. Check your connection.", false);
  }
}

async function settingsSavePassword() {
  const current  = document.getElementById("settingsCurrentPassword")?.value?.trim();
  const nuevo    = document.getElementById("settingsNewPassword")?.value?.trim();
  const confirm  = document.getElementById("settingsConfirmPassword")?.value?.trim();
  const msg      = document.getElementById("settingsPasswordMsg");

  const showMsg = (text, ok) => {
    if (!msg) return;
    msg.textContent   = text;
    msg.style.color   = ok ? "green" : "red";
    msg.style.display = "block";
  };

  if (!current || !nuevo || !confirm) {
    showMsg("Please fill in all three fields.", false);
    return;
  }

  const token = localStorage.getItem("access");
  try {
    const res = await fetch(CHANGE_PASSWORD_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`
      },
      body: JSON.stringify({
        current_password: current,
        new_password:     nuevo,
        confirm_password: confirm
      })
    });
    if (res.status === 401) { handleSessionExpired(); return; }
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      showMsg("Password updated successfully.", true);
      ["settingsCurrentPassword", "settingsNewPassword", "settingsConfirmPassword"]
        .forEach((id) => { const el = document.getElementById(id); if (el) el.value = ""; });
    } else {
      const err =
        data?.current_password?.[0] ||
        data?.confirm_password?.[0] ||
        data?.new_password?.[0]     ||
        data?.detail                ||
        "Error changing password.";
      showMsg(err, false);
    }
  } catch {
    showMsg("Network error. Check your connection.", false);
  }
}

async function loadMyProfile() {
  try {
    const response = await apiFetch(ME_URL);
    if (!response.ok) {
      throw new Error(`Error HTTP: ${response.status}`);
    }
    const user = await response.json();
    const stored = getCurrentUser();
    localStorage.setItem("user", JSON.stringify({ ...stored, ...user }));
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar perfil:", err);
  }
}

if (createUserForm) {
  createUserForm.addEventListener("submit", handleAdminSubmit);
}

// Handler central del submit para ADMIN (preserva la lógica existente)
async function handleAdminSubmit(e) {
  e.preventDefault();
  const fullName = document.getElementById("fullNameInput").value.trim();
  const email = document.getElementById("emailInput").value.trim();
  const role = document.getElementById("roleSelect").value.trim().toLowerCase();
  const status = document.getElementById("statusSelect").value.trim().toLowerCase();

  if (!fullName || !email || !role || !status) {
    alert("Completá todos los campos obligatorios.");
    return;
  }

  const isEditing = editingUserId !== null;
  // Password opcional al crear: si se deja vacío, el backend usa una
  // temporal (ADMIN_CREATED_USER_PASSWORD) y fuerza su cambio. No se manda
  // la clave "password" en el body si está vacía (un CharField vacío
  // explícito sería inválido para DRF; omitirla activa el fallback).
  const password = isEditing ? "" : document.getElementById("passwordInput").value.trim();
  const forcePasswordChange = document.getElementById("forcePasswordChangeInput")?.checked || false;

  const submitBtn = document.getElementById("userFormSubmit");
  const originalText = submitBtn.textContent;
  submitBtn.disabled = true;
  submitBtn.textContent = editingUserId ? "Guardando..." : "Creando...";

  const url = isEditing ? `${API_URL}${editingUserId}/` : API_URL;
  const method = isEditing ? "PATCH" : "POST";

  const body = { email, full_name: fullName, role, status, force_password_change: forcePasswordChange };
  if (!isEditing && password) body.password = password;

  try {
    const response = await apiFetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const messages = [];
      if (data && typeof data === "object") {
        for (const [field, errors] of Object.entries(data)) {
          if (Array.isArray(errors)) messages.push(`${field}: ${errors.join(", ")}`);
          else if (typeof errors === "string") messages.push(`${field}: ${errors}`);
        }
      }
      throw new Error(messages.length ? messages.join("\n") : `Error HTTP: ${response.status}`);
    }

    panel.style.display = "none";
    resetCreateForm();
    currentPage = 1;
    loadUsers();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error(isEditing ? "Error al editar usuario:" : "Error al crear usuario:", err);
    alert(err.message || (isEditing ? "No se pudo editar el usuario." : "No se pudo crear el usuario."));
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = originalText;
  }
}

// ---------------------------------------------------------------------------
// ROLES Y PERMISOS: administración exclusiva para ADMIN.
// ---------------------------------------------------------------------------
const ROLE_DEFINITIONS = [
  { key: "admin", label: "Administrador" },
  { key: "designer", label: "Diseñador" },
  { key: "operator", label: "Operador" },
  { key: "subscriber", label: "Suscriptor" },
];
const PERMISSION_GROUPS = [
  {
    label: "USUARIOS",
    permissions: [
      ["users.view", "Ver usuarios"],
      ["users.create", "Crear usuarios"],
      ["users.edit", "Editar usuarios"],
      ["users.deactivate", "Desactivar usuarios"],
      ["users.reactivate", "Reactivar usuarios"],
    ],
  },
  {
    label: "PERFIL PROPIO",
    permissions: [
      ["users.me.view", "Ver perfil propio"],
      ["users.me.edit", "Editar perfil propio"],
      ["users.me.change_password", "Cambiar contraseña propia"],
    ],
  },
];
const MANAGEABLE_ROLE_KEYS = new Set(ROLE_DEFINITIONS.map((role) => role.key));
const AVAILABLE_PERMISSION_CODES = new Set(
  PERMISSION_GROUPS.flatMap((group) => group.permissions.map(([code]) => code))
);
let roles = [];
let permissionCatalog = [];
let selectedRole = null;
let rolesLoaded = false;
let permissionsLoaded = false;

function showRolesMessage(text, type = "error") {
  const message = document.getElementById("rolesMessage");
  if (!message) return;
  message.textContent = text;
  message.className = `roles-message ${type}`;
  message.style.display = "block";
}

function clearRolesMessage() {
  const message = document.getElementById("rolesMessage");
  if (!message) return;
  message.textContent = "";
  message.className = "roles-message";
  message.style.display = "none";
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

function getRoleId(role) {
  return role?.id ?? role?.pk;
}

function setRolesLoading(text) {
  const list = document.getElementById("rolesList");
  if (list) list.innerHTML = `<p class="roles-loading">${text}</p>`;
}

function setPermissionsLoading(text) {
  const content = document.getElementById("rolePermissionsContent");
  if (content) content.innerHTML = `<p class="roles-loading">${text}</p>`;
  const saveButton = document.getElementById("saveRolePermissions");
  if (saveButton) saveButton.disabled = true;
}

function renderRoles() {
  const list = document.getElementById("rolesList");
  if (!list) return;

  list.innerHTML = "";
  roles.forEach((role) => {
    const isBasic = MANAGEABLE_ROLE_KEYS.has(role.key);
    const row = document.createElement("div");
    row.className = "role-selector-wrap";

    const button = document.createElement("button");
    button.type = "button";
    button.className = "role-selector";
    button.textContent = role.label;
    button.dataset.roleId = getRoleId(role);
    button.classList.toggle("active", getRoleId(selectedRole) === getRoleId(role));
    button.addEventListener("click", () => loadRolePermissions(getRoleId(role)));
    row.appendChild(button);

    // Botón "Eliminar" solo para roles personalizados (no básicos).
    if (!isBasic) {
      const deleteBtn = document.createElement("button");
      deleteBtn.type = "button";
      deleteBtn.className = "role-delete-btn";
      deleteBtn.textContent = "Eliminar";
      deleteBtn.dataset.roleId = getRoleId(role);
      deleteBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteRole(getRoleId(role));
      });
      row.appendChild(deleteBtn);
    }

    list.appendChild(row);
  });
}

function renderRolePermissions(permissionCodes = []) {
  const content = document.getElementById("rolePermissionsContent");
  const saveButton = document.getElementById("saveRolePermissions");
  if (!content) return;

  const assigned = new Set(
    permissionCodes.map((permission) =>
      typeof permission === "string" ? permission : permission.code || permission.key || permission.name
    )
  );
  const catalogCodes = new Set(
    permissionCatalog.map((permission) =>
      typeof permission === "string" ? permission : permission.code || permission.key || permission.name
    )
  );

  content.innerHTML = "";
  PERMISSION_GROUPS.forEach((group) => {
    const groupEl = document.createElement("section");
    groupEl.className = "permission-group";
    const title = document.createElement("h3");
    title.textContent = group.label;
    groupEl.appendChild(title);

    group.permissions.forEach(([code, label]) => {
      if (!catalogCodes.has(code)) return;
      const option = document.createElement("label");
      option.className = "permission-option";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = code;
      checkbox.checked = assigned.has(code);
      const text = document.createElement("span");
      text.textContent = label;
      option.append(checkbox, text);
      groupEl.appendChild(option);
    });
    content.appendChild(groupEl);
  });

  if (saveButton) saveButton.disabled = !selectedRole;
}

async function loadPermissionCatalog() {
  setPermissionsLoading("Cargando permisos...");
  const response = await apiFetch(PERMISSIONS_URL);
  const data = await response.json().catch(() => ({}));
  if (response.status === 403) throw new Error("No tenés permisos para administrar roles.");
  if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo cargar el catálogo de permisos."));

  const catalog = Array.isArray(data) ? data : data.results || data.permissions || [];
  permissionCatalog = catalog.filter((permission) => {
    const code = typeof permission === "string" ? permission : permission.code || permission.key || permission.name;
    return AVAILABLE_PERMISSION_CODES.has(code);
  });
  permissionsLoaded = true;
}

async function loadRoles() {
  setRolesLoading("Cargando roles...");
  const response = await apiFetch(ROLES_URL);
  const data = await response.json().catch(() => ({}));
  if (response.status === 403) throw new Error("No tenés permisos para administrar roles.");
  if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar los roles."));

  const roleList = Array.isArray(data) ? data : data.results || data.roles || [];
  roles = roleList.map((role) => {
    const key = String(role.key || role.name).toLowerCase();
    const definition = ROLE_DEFINITIONS.find((d) => d.key === key);
    return {
      ...role,
      key,
      label: definition?.label || role.label || key.charAt(0).toUpperCase() + key.slice(1),
    };
  });
  rolesLoaded = true;
  renderRoles();
}

async function loadRolePermissions(roleId) {
  if (!isAdminMode()) return;
  const role = roles.find((item) => String(getRoleId(item)) === String(roleId));
  if (!role) {
    showRolesMessage("El rol solicitado no existe.");
    return;
  }

  selectedRole = role;
  renderRoles();
  setPermissionsLoading("Cargando permisos...");
  clearRolesMessage();
  try {
    const response = await apiFetch(`${ROLES_URL}${getRoleId(role)}/permissions/`);
    const data = await response.json().catch(() => ({}));
    if (getRoleId(selectedRole) !== getRoleId(role)) return;
    if (response.status === 403) throw new Error("No tenés permisos para administrar roles.");
    if (response.status === 404) throw new Error("El rol solicitado no existe.");
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron cargar los permisos del rol."));
    selectedRole = { ...selectedRole, ...(data.role || {}), permissions: data.permissions || [] };
    roles = roles.map((item) => getRoleId(item) === getRoleId(selectedRole) ? selectedRole : item);
    renderRoles();
    renderRolePermissions(selectedRole.permissions);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar permisos del rol:", err);
    setPermissionsLoading("No se pudieron cargar los permisos.");
    showRolesMessage(err.message || "No se pudieron cargar los permisos del rol.");
  }
}

async function openRoles() {
  if (!isAdminMode()) {
    closeRoles();
    return;
  }

  closeSettings();
  closeReports();
  document.querySelectorAll(".nav a").forEach((link) => link.classList.remove("active"));
  document.getElementById("navRoles")?.classList.add("active");
  [".topbar", ".stats", ".table-card"].forEach((selector) => {
    const element = document.querySelector(selector);
    if (element) element.style.display = "none";
  });
  document.getElementById("rolesSection").style.display = "";
  clearRolesMessage();

  if (rolesLoaded && permissionsLoaded) {
    renderRoles();
    if (selectedRole) renderRolePermissions(selectedRole.permissions || []);
    return;
  }

  try {
    await loadRoles();
    await loadPermissionCatalog();
    if (!roles.length) throw new Error("No hay roles administrables para mostrar.");
    await loadRolePermissions(getRoleId(roles[0]));
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al abrir Roles:", err);
    setPermissionsLoading("No se pudieron cargar los permisos.");
    showRolesMessage(err.message || "No se pudo cargar la administración de roles.");
  }
}

function closeRoles() {
  const section = document.getElementById("rolesSection");
  if (section) section.style.display = "none";
  // Restaurar la tabla de usuarios si el usuario tiene users.view,
  // independientemente de si es admin o no.
  if (canViewUsers()) {
    [".topbar", ".stats", ".table-card"].forEach((selector) => {
      const element = document.querySelector(selector);
      if (element) element.style.display = "";
    });
  }
}

// ---------------------------------------------------------------------------
// REPORTES / MÉTRICAS: mismo patrón de toggle de sección que Roles/Ajustes.
// ---------------------------------------------------------------------------
function renderMetricsBars(containerId, items, { labelKey, countKey, labelFormatter }) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!items || items.length === 0) {
    container.innerHTML = `<p class="roles-loading">Sin datos para mostrar.</p>`;
    return;
  }
  const max = Math.max(...items.map((item) => item[countKey]), 1);
  container.innerHTML = items
    .map((item) => {
      const label = labelFormatter ? labelFormatter(item[labelKey]) : item[labelKey];
      const pct = Math.round((item[countKey] / max) * 100);
      return `
        <div class="metrics-bar-row">
          <span class="metrics-bar-label" title="${label}">${label}</span>
          <span class="metrics-bar-track"><span class="metrics-bar-fill" style="width:${pct}%"></span></span>
          <span class="metrics-bar-count">${item[countKey]}</span>
        </div>
      `;
    })
    .join("");
}

function renderAuthMethodStats(authMethod) {
  const container = document.getElementById("metricsAuthMethod");
  if (!container) return;
  const local = authMethod?.local ?? 0;
  const google = authMethod?.google ?? 0;
  container.innerHTML = `
    <div class="metrics-stat">
      <span class="metrics-stat-value">${local}</span>
      <span class="metrics-stat-label">Local (email/password)</span>
    </div>
    <div class="metrics-stat">
      <span class="metrics-stat-value">${google}</span>
      <span class="metrics-stat-label">Google</span>
    </div>
  `;
}

async function loadMetrics() {
  ["metricsRoleDistribution", "metricsSignupsByMonth", "metricsAuthMethod"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = `<p class="roles-loading">Cargando métricas...</p>`;
  });

  try {
    const response = await apiFetch(METRICS_URL);
    const data = await response.json().catch(() => ({}));
    if (response.status === 403) throw new Error("No tenés permisos para ver las métricas.");
    if (!response.ok) throw new Error(data.detail || "No se pudieron cargar las métricas.");

    renderMetricsBars("metricsRoleDistribution", data.role_distribution, {
      labelKey: "label", countKey: "count",
    });
    renderMetricsBars("metricsSignupsByMonth", data.signups_by_month, {
      labelKey: "month", countKey: "count",
    });
    renderAuthMethodStats(data.auth_method);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar métricas:", err);
    ["metricsRoleDistribution", "metricsSignupsByMonth", "metricsAuthMethod"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = `<p class="roles-loading">${err.message || "No se pudieron cargar las métricas."}</p>`;
    });
  }
}

function openReports() {
  closeSettings();
  closeRoles();
  document.querySelectorAll(".nav a").forEach((link) => link.classList.remove("active"));
  document.getElementById("navReports")?.classList.add("active");
  [".topbar", ".stats", ".table-card"].forEach((selector) => {
    const element = document.querySelector(selector);
    if (element) element.style.display = "none";
  });
  document.getElementById("reportsSection").style.display = "";
  loadMetrics();
}

function closeReports() {
  const section = document.getElementById("reportsSection");
  if (section) section.style.display = "none";
  if (canViewUsers()) {
    [".topbar", ".stats", ".table-card"].forEach((selector) => {
      const element = document.querySelector(selector);
      if (element) element.style.display = "";
    });
  }
}

async function saveRolePermissions() {
  if (!isAdminMode() || !selectedRole) return;
  const button = document.getElementById("saveRolePermissions");
  const selectedPermissions = Array.from(
    document.querySelectorAll('#rolePermissionsContent input[type="checkbox"]:checked')
  ).map((checkbox) => checkbox.value);
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Guardando...";
  clearRolesMessage();

  try {
    const response = await apiFetch(`${ROLES_URL}${getRoleId(selectedRole)}/permissions/`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permissions: selectedPermissions }),
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 403) throw new Error("No tenés permisos para administrar roles.");
    if (response.status === 404) throw new Error("El rol solicitado no existe.");
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudieron guardar los permisos."));

    selectedRole = { ...selectedRole, ...(data.role || {}), permissions: data.permissions || selectedPermissions };
    roles = roles.map((role) => getRoleId(role) === getRoleId(selectedRole) ? selectedRole : role);
    renderRoles();
    renderRolePermissions(selectedRole.permissions);
    // El guardado no debe cambiar de vista ni volver a Users: conserva el rol
    // seleccionado y reafirma la sección activa sin recargar la página.
    document.getElementById("rolesSection").style.display = "";
    [".topbar", ".stats", ".table-card"].forEach((selector) => {
      const element = document.querySelector(selector);
      if (element) element.style.display = "none";
    });
    document.querySelectorAll(".nav a").forEach((link) => link.classList.remove("active"));
    document.getElementById("navRoles")?.classList.add("active");
    showRolesMessage("Permisos guardados correctamente.", "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al guardar permisos del rol:", err);
    showRolesMessage(err.message || "No se pudieron guardar los permisos.");
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

// ---------------------------------------------------------------------------
// CREAR ROL PERSONALIZADO
// ---------------------------------------------------------------------------
async function createRole() {
  if (!isAdminMode()) return;
  const input = document.getElementById("newRoleName");
  const name = input?.value?.trim();
  if (!name) {
    showRolesMessage("Ingresá un nombre para el nuevo rol.");
    return;
  }

  const button = document.getElementById("createRoleBtn");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Creando...";
  clearRolesMessage();

  try {
    const response = await apiFetch(ROLES_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const data = await response.json().catch(() => ({}));
    if (response.status === 403) throw new Error("No tenés permisos para administrar roles.");
    if (!response.ok) throw new Error(getErrorMessage(data, "No se pudo crear el rol."));

    // Limpiar el input y recargar la lista de roles.
    if (input) input.value = "";
    rolesLoaded = false;
    await loadRoles();
    // Seleccionar el rol recién creado para poder asignarle permisos.
    const created = roles.find((role) => String(getRoleId(role)) === String(getRoleId(data)));
    if (created) {
      await loadRolePermissions(getRoleId(created));
    }
    // Permanecer en Roles (no redirigir a Usuarios).
    document.getElementById("rolesSection").style.display = "";
    [".topbar", ".stats", ".table-card"].forEach((selector) => {
      const element = document.querySelector(selector);
      if (element) element.style.display = "none";
    });
    document.querySelectorAll(".nav a").forEach((link) => link.classList.remove("active"));
    document.getElementById("navRoles")?.classList.add("active");
    showRolesMessage(`Rol "${data.name || name}" creado correctamente.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al crear rol:", err);
    showRolesMessage(err.message || "No se pudo crear el rol.");
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

// ---------------------------------------------------------------------------
// ELIMINAR ROL PERSONALIZADO
// ---------------------------------------------------------------------------
async function deleteRole(roleId) {
  if (!isAdminMode()) return;
  const role = roles.find((item) => String(getRoleId(item)) === String(roleId));
  if (!role) {
    showRolesMessage("El rol solicitado no existe.");
    return;
  }

  const confirmed = window.confirm(
    `¿Eliminar el rol "${role.label}"?\n\nLos usuarios que pertenecían a este rol pasarán a "Suscriptor". Esta acción no se puede deshacer.`
  );
  if (!confirmed) return;

  try {
    const response = await apiFetch(`${ROLES_URL}${roleId}/`, {
      method: "DELETE",
    });
    if (response.status === 403) throw new Error("No tenés permisos para administrar roles.");
    if (response.status === 404) throw new Error("El rol solicitado no existe.");
    if (response.status === 400) {
      const data = await response.json().catch(() => ({}));
      throw new Error(getErrorMessage(data, "No se pudo eliminar el rol."));
    }
    if (!response.ok) throw new Error(`Error HTTP: ${response.status}`);

    // Si el rol eliminado era el seleccionado, limpiar la selección.
    if (selectedRole && String(getRoleId(selectedRole)) === String(roleId)) {
      selectedRole = null;
    }
    // Recargar la lista de roles (permaneciendo en Roles).
    rolesLoaded = false;
    await loadRoles();
    if (roles.length) {
      await loadRolePermissions(getRoleId(roles[0]));
    } else {
      setPermissionsLoading("No hay roles disponibles.");
    }
    // Permanecer en Roles (no redirigir a Usuarios).
    document.getElementById("rolesSection").style.display = "";
    [".topbar", ".stats", ".table-card"].forEach((selector) => {
      const element = document.querySelector(selector);
      if (element) element.style.display = "none";
    });
    document.querySelectorAll(".nav a").forEach((link) => link.classList.remove("active"));
    document.getElementById("navRoles")?.classList.add("active");
    showRolesMessage(`Rol "${role.label}" eliminado correctamente.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al eliminar rol:", err);
    showRolesMessage(err.message || "No se pudo eliminar el rol.");
  }
}

// ---------------------------------------------------------------------------
// SETTINGS: conectar listeners
// ---------------------------------------------------------------------------
document.getElementById("navRoles")
  ?.addEventListener("click", (event) => {
    event.preventDefault();
    openRoles();
  });
document.getElementById("saveRolePermissions")
  ?.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    saveRolePermissions();
  });
document.getElementById("createRoleBtn")
  ?.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    createRole();
  });
document.getElementById("navSettings")
  ?.addEventListener("click", () => {
    closeRoles();
    closeReports();
    openSettings();
  });
document.getElementById("navReports")
  ?.addEventListener("click", (event) => {
    event.preventDefault();
    openReports();
  });

// Botones dentro de Settings
document.getElementById("settingsSaveProfile")
  ?.addEventListener("click", settingsSaveProfile);
document.getElementById("settingsSavePassword")
  ?.addEventListener("click", settingsSavePassword);

// Los demás ítems cierran las vistas mutuamente excluyentes.
document.querySelectorAll(".nav a:not(#navSettings):not(#navRoles):not(#navReports)").forEach((a) => {
  a.addEventListener("click", () => {
    closeSettings();
    closeRoles();
    closeReports();
  });
});

// ---------------------------------------------------------------------------
// 🚀 INICIO: esta pantalla es exclusiva de quien puede gestionar usuarios.
// Sin el permiso users.view, se redirige al dashboard (el perfil propio
// vive en perfil.html).
// ---------------------------------------------------------------------------
function bootstrap() {
  if (!canViewUsers()) {
    window.location.replace("dashboard.html");
    return;
  }

  const adminMode = isAdminMode();
  document.getElementById("openCreatePanel").style.display = canUseUserPermission("users.create") ? "" : "none";
  if (!adminMode) document.getElementById("navRoles").style.display = "none";
  loadMyProfile();
  loadUsers();
  // Cargar dinámicamente TODOS los roles (básicos + personalizados).
  loadRoleOptions();
}

bootstrap();
