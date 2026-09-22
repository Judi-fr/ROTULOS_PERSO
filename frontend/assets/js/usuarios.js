const statusLabel = {
  active: "Activo",
  inactive: "Inactivo",
};
// Clave de la clase CSS del badge de rol: SIEMPRE el texto original que
// manda el backend (role_label, en inglés). No traducir estas claves —
// solo se traduce el texto que se muestra (ver translateRole, en
// assets/js/admin_common.js).
const roleClass = {
  Admin: "admin",
  Designer: "designer",
  Operator: "operator",
  Subscriber: "subscriber",
};
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
const API_URL = `${window.APP_CONFIG.API_BASE}/users/`;
const ME_URL = `${window.APP_CONFIG.API_BASE}/auth/me/`;
const ROLES_URL = `${window.APP_CONFIG.API_BASE}/auth/roles/`;
const EXPORT_CSV_URL = `${window.APP_CONFIG.API_BASE}/users/export/`;

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
      ${escapeHtml(message)}
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
          <img class="user-avatar" src="https://api.dicebear.com/7.x/avataaars/svg?seed=${encodeURIComponent(u.email || u.id)}" alt="">
          <span class="user-name" title="${escapeHtml(u.display_name || u.email)}">${escapeHtml(u.display_name || u.email)}</span>
        </div>
      </td>
      <td class="cell-muted" title="${escapeHtml(u.email || "")}">${escapeHtml(u.email)}</td>
      <td><span class="badge ${roleClass[role] || ""}">${escapeHtml(roleText)}</span></td>
      <td class="status-cell"><span class="status ${statusKey}"><span class="dot"></span>${escapeHtml(statusText)}</span>${lockedLine}</td>
      <td class="cell-muted">${escapeHtml(u.created_date || "-")}</td>
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
              <span style="flex:0 0 auto; max-width:55%; font-size:12.5px; font-weight:600; color:#64748b; line-height:1.35;">${escapeHtml(label)}</span>
              <span style="flex:1; font-size:14px; color:#0f172a; text-align:right; word-break:break-word;">${escapeHtml(value)}</span>
            </div>
          `
        )
        .join("");
    })
    .catch((err) => {
      if (err.isSessionExpired) return;
      console.error("Error al ver usuario:", err);
      content.innerHTML = `<div style="text-align:center; color:#dc2626; padding:20px;">${escapeHtml(err.message)}</div>`;
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
// INICIO: esta pantalla es exclusiva de quien puede gestionar usuarios. Sin
// el permiso users.view, se redirige al dashboard (el perfil propio vive en
// perfil.html, y Roles/Reportes/Auditoría/Soporte ya son páginas propias:
// ver assets/js/admin_sidebar.js para el gateo de esos links).
// ---------------------------------------------------------------------------
function bootstrap() {
  if (!canViewUsers()) {
    window.location.replace("dashboard.html");
    return;
  }

  // Compatibilidad: enlaces viejos guardados como gestionuser.html#audit /
  // #support (antes anclas dentro de esta misma página) van a las páginas
  // nuevas correspondientes.
  if (window.location.hash === "#audit") {
    window.location.replace("auditoria.html");
    return;
  }
  if (window.location.hash === "#support") {
    window.location.replace("soporte_admin.html");
    return;
  }

  document.getElementById("openCreatePanel").style.display = canUseUserPermission("users.create") ? "" : "none";
  loadMyProfile();
  loadUsers();
  // Cargar dinámicamente TODOS los roles (básicos + personalizados).
  loadRoleOptions();
}

bootstrap();
