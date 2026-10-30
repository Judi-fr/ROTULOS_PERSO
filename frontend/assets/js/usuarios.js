const statusLabel = {
  active: "Activo",
  inactive: "Inactivo",
};
// Clave de la clase CSS del badge de rol: SIEMPRE el texto original que
// manda el backend (role_label, en inglés). No traducir estas claves —
// solo se traduce el texto que se muestra (ver translateRole, en
// assets/js/admin_common.js).
// Un rol que no es ninguno de los dos fijos es personal de la plataforma
// (rol personalizado, 2026-10-21): lleva la clase "staff".
const roleClass = {
  Admin: "admin",
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
  updateFilterIndicators();
  tbody.innerHTML = `<tr><td colspan="8" class="table-placeholder"><span class="app-skeleton" role="status" aria-label="Cargando…"><span></span><span></span><span></span></span></td></tr>`;

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
      const filtered = activeFilterCount() > 0 || currentSearch;
      tbody.innerHTML = `<tr><td colspan="8" class="table-placeholder">${appEmptyState(
        filtered
          ? { icon: "list", title: "Nadie coincide con la búsqueda", text: "Probá con otra búsqueda o sacá algún filtro." }
          : { icon: "list", title: "Todavía no hay usuarios", text: "Los que se registren o crees con «Crear usuario» aparecen acá." }
      )}</td></tr>`;
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
    tbody.innerHTML = `<tr><td colspan="8" class="table-placeholder table-error">${escapeHtml(message)}</td></tr>`;
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
    tr.style.setProperty("--row", Math.min(i, 12));
    const role = u.role_label || "User";
    const roleText = translateRole(role);
    const statusKey = u.status_key || (u.is_active ? "active" : "inactive");
    const statusText = statusLabel[statusKey] || u.status_label || statusKey;
    const isSelf = Number(u.id) === currentUserId;
    const lockedLine = u.is_locked
      ? `<div style="font-size:11px; color:var(--info-500); margin-top:2px;">${formatLockoutRemaining(u.lockout_remaining_seconds)}</div>`
      : "";
    // El admin NO puede desactivarse/eliminarse a sí mismo (protección doble:
    // backend devuelve 403 y el frontend no muestra siquiera la opción).
    const deleteAction = !canDeactivateUsers
      ? ""
      : isSelf
      ? `<span class="self-protected" style="font-size:12px; color:var(--subtle);" title="No podés desactivar tu propia cuenta">No podés eliminarte</span>`
      : `<a href="#" class="danger delete-user" data-delete-id="${u.id}"><i class="ph ph-trash" aria-hidden="true"></i>Eliminar</a>`;

    tr.innerHTML = `
      <td><input type="checkbox" class="row-checkbox" data-user-id="${u.id}"></td>
      <td>
        <div class="user-cell">
          <img class="user-avatar" src="${personAvatarUrl(u)}" alt="">
          <div class="user-ident">
            <span class="user-name" title="${escapeHtml(u.display_name || u.email)}">${escapeHtml(u.display_name || u.email)}</span>
            ${u.display_name && u.display_name !== u.email ? `<span class="user-email" title="${escapeHtml(u.email || "")}">${escapeHtml(u.email || "")}</span>` : ""}
          </div>
        </div>
      </td>
      <td><span class="badge ${roleClass[role] || "staff"}">${escapeHtml(roleText)}</span></td>
      <td class="status-cell"><span class="status ${statusKey}"><span class="dot"></span>${escapeHtml(statusText)}</span>${lockedLine}</td>
      <td class="cell-muted">${escapeHtml(u.created_date || "-")}</td>
      <td class="num-col${u.orders_count ? "" : " cell-muted"}">${u.orders_count ?? 0}</td>
      <td class="cell-muted">${formatLastOrderDate(u.last_order_at)}</td>
      <td class="actions-col">
        <div class="row-actions">
          <button class="more-btn" data-idx="${i}">
            <i class="ph ph-dots-three-vertical" aria-hidden="true"></i>
          </button>
          <div class="dropdown" id="dd-${i}" style="display:none;">
            <a href="#" class="view-user" data-view-id="${u.id}"><i class="ph ph-eye" aria-hidden="true"></i>Ver</a>
            ${canEditUsers ? `<a href="#" class="edit-user" data-edit-id="${u.id}"><i class="ph ph-pencil-simple" aria-hidden="true"></i>Editar</a>` : ""}
            ${canEditUsers ? `<a href="comercio_usuario.html?user=${u.id}"><i class="ph ph-storefront" aria-hidden="true"></i>Comercio</a>` : ""}
            <div class="sep"></div>
            ${canReactivateUsers && u.can_reactivate ? `<a href="#" class="reactivate-user" data-reactivate-id="${u.id}"><i class="ph ph-arrows-clockwise" aria-hidden="true"></i>Reactivar</a>` : ""}
            ${canUnlockUsers && u.is_locked ? `<a href="#" class="unlock-user" data-unlock-id="${u.id}"><i class="ph ph-lock-key-open" aria-hidden="true"></i>Unlock</a>` : ""}
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
  const open = count > 0;
  bar.classList.toggle("is-open", open);
  bar.inert = !open;
  bar.setAttribute("aria-hidden", open ? "false" : "true");
  const numEl = document.getElementById("bulkActionsNum");
  if (numEl) numEl.textContent = String(count);
  if (countEl) countEl.textContent = count === 1 ? "seleccionado" : "seleccionados";
}

function clearSelection() {
  selectedUserIds.clear();
  document.querySelectorAll(".row-checkbox").forEach((cb) => {
    cb.checked = false;
    cb.closest("tr")?.classList.remove("row-selected");
  });
  updateSelectAllCheckboxState();
  updateBulkActionsBar();
}

document.getElementById("bulkClearBtn")?.addEventListener("click", clearSelection);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && selectedUserIds.size > 0 && !document.querySelector("dialog[open]")) clearSelection();
});

async function runBulkAction(payload) {
  if (selectedUserIds.size === 0) {
    appAlert("Seleccioná al menos un usuario (tildá su casilla).", { tone: "info" });
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
    appAlert(message);
    loadUsers();
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error en la acción masiva:", err);
    appAlert(err.message || "No se pudo completar la acción masiva.");
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
    appAlert("Elegí un rol para aplicar.", { tone: "info" });
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
// EXPORTAR: un solo botón (antes había dos que no se distinguían: "Exportar"
// armaba un Excel en el navegador con los tildados y "Exportar CSV" bajaba del
// servidor). El panel pregunta qué usuarios (toda la lista con los filtros de
// ahora, o los tildados) y el formato; el archivo lo arma siempre el servidor
// (users/export/?file_type=csv|xlsx), sin límite de página.
// ---------------------------------------------------------------------------
const exportUsersBtn = document.getElementById("exportUsersBtn");
const exportUsersPanel = document.getElementById("exportUsersPanel");
let lastTotalCount = null;

function refreshExportPanel() {
  const selectedRadio = document.querySelector('input[name="exportScope"][value="selected"]');
  const filteredRadio = document.querySelector('input[name="exportScope"][value="filtered"]');
  const count = selectedUserIds.size;
  if (selectedRadio) {
    selectedRadio.disabled = count === 0;
    if (count === 0 && selectedRadio.checked && filteredRadio) filteredRadio.checked = true;
    // Con usuarios tildados, lo más probable es que quiera esos.
    if (count > 0 && exportUsersPanel?.hidden) selectedRadio.checked = true;
  }
  const selectedHint = document.getElementById("exportSelectedHint");
  if (selectedHint) {
    selectedHint.textContent = count ? `${count} usuario${count === 1 ? "" : "s"} tildado${count === 1 ? "" : "s"}` : "Tildá usuarios en la tabla";
  }
  const filteredHint = document.getElementById("exportFilteredHint");
  if (filteredHint) {
    const filtered = activeFilterCount() > 0 || currentSearch;
    const people = lastTotalCount == null ? "" : `${lastTotalCount} usuario${lastTotalCount === 1 ? "" : "s"}`;
    filteredHint.textContent = [people, filtered ? "con la búsqueda y los filtros de ahora" : "sin filtros"].filter(Boolean).join(", ");
  }
}

function setExportPanelOpen(open) {
  if (!exportUsersPanel || !exportUsersBtn) return;
  if (open) refreshExportPanel();
  exportUsersPanel.hidden = !open;
  exportUsersBtn.setAttribute("aria-expanded", String(open));
}

exportUsersBtn?.addEventListener("click", (e) => {
  e.stopPropagation();
  setExportPanelOpen(exportUsersPanel.hidden);
});
exportUsersPanel?.addEventListener("click", (e) => e.stopPropagation());
document.addEventListener("click", () => setExportPanelOpen(false));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && exportUsersPanel && !exportUsersPanel.hidden) {
    setExportPanelOpen(false);
    exportUsersBtn.focus();
  }
});

document.getElementById("exportUsersGo")?.addEventListener("click", async (e) => {
  const button = e.currentTarget;
  const scope = document.querySelector('input[name="exportScope"]:checked')?.value || "filtered";
  const format = document.querySelector('input[name="exportFormat"]:checked')?.value || "xlsx";
  if (scope === "selected" && selectedUserIds.size === 0) {
    appAlert("Tildá al menos un usuario en la tabla.", { tone: "info" });
    return;
  }

  const params = new URLSearchParams(buildQueryString("", { includePage: false }).replace(/^\?/, ""));
  params.set("file_type", format);
  if (scope === "selected") params.set("ids", Array.from(selectedUserIds).join(","));

  const original = button.textContent;
  button.disabled = true;
  button.textContent = "Preparando…";
  try {
    const response = await apiFetch(`${EXPORT_CSV_URL}?${params.toString()}`);
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(getErrorMessage(data, `No se pudo exportar (error ${response.status}).`));
    }
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = `usuarios-${new Date().toISOString().slice(0, 10)}.${format}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
    setExportPanelOpen(false);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al exportar usuarios:", err);
    appAlert(err.message || "No se pudo exportar.");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
});

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
  lastTotalCount = count ?? null;
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

  // Barra del total: la parte activa y la inactiva, en proporción.
  const total = Number(summary.total) || 0;
  const share = (value) => (total ? `${((Number(value) || 0) / total) * 100}%` : "0%");
  document.getElementById("pulseBarActive")?.style.setProperty("width", share(summary.active));
  document.getElementById("pulseBarInactive")?.style.setProperty("width", share(summary.inactive));
  const activePct = total ? Math.round(((Number(summary.active) || 0) / total) * 100) : 0;
  document.getElementById("usersPulseBar")?.setAttribute("aria-label", `${activePct} % activos`);
  const note = document.getElementById("pulseNote");
  if (note) note.textContent = total ? `${activePct} % con la cuenta activa` : "Todavía no hay usuarios";
  document.querySelector(".pulse-stat-locked")?.classList.toggle("has-locked", Number(summary.locked) > 0);
}

// Cuántos filtros hay puestos (el orden no cuenta): se muestra en el botón
// "Filtros", y los atajos del resumen se ven apretados si coinciden.
function activeFilterCount() {
  return [
    currentStatus !== "all",
    currentRole !== "all",
    Boolean(currentDateJoinedFrom),
    Boolean(currentDateJoinedTo),
    Boolean(currentLastLoginFrom),
    Boolean(currentLastLoginTo),
    currentNeverLoggedIn,
    Boolean(currentInactiveDays),
    currentLocked !== "all",
    currentFailedAttempts !== "all",
    currentAuthMethod !== "all",
  ].filter(Boolean).length;
}

function updateFilterIndicators() {
  const count = activeFilterCount();
  const badge = document.getElementById("userFilterCount");
  if (badge) {
    badge.hidden = count === 0;
    badge.textContent = String(count);
  }
  document.querySelectorAll("[data-summary-filter]").forEach((button) => {
    const kind = button.dataset.summaryFilter;
    const pressed = kind === "locked" ? currentLocked === "true" : currentStatus === kind;
    button.setAttribute("aria-pressed", String(pressed));
  });
}

// Atajos del resumen: Activos / Inactivos filtran por estado y Bloqueados por
// bloqueo; tocar el que ya está puesto lo saca. Usan los mismos selects del
// panel de filtros, así los dos lugares dicen lo mismo.
document.querySelectorAll("[data-summary-filter]").forEach((button) => {
  button.addEventListener("click", () => {
    const kind = button.dataset.summaryFilter;
    const on = button.getAttribute("aria-pressed") !== "true";
    if (kind === "locked") {
      currentLocked = on ? "true" : "all";
      const select = document.getElementById("userLockedFilter");
      if (select) select.value = currentLocked;
    } else {
      currentStatus = on ? kind : "all";
      const select = document.getElementById("userStatusFilter");
      if (select) select.value = currentStatus;
    }
    currentPage = 1;
    loadUsers();
  });
});

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

async function reactivateUser(userId) {
  const user = lastUsers.find((u) => Number(u.id) === userId);
  const userName = user ? user.display_name || user.email : `#${userId}`;

  const confirmed = await appConfirm(
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
      appAlert(`El usuario "${userName}" fue reactivado correctamente.`, { tone: "success" });
      // Recargar la lista manteniendo la página actual si sigue siendo válida.
      loadUsers();
    })
    .catch((err) => {
      if (err.isSessionExpired) return;
      console.error("Error al reactivar usuario:", err);
      appAlert(err.message || "No se pudo reactivar el usuario. Intentá de nuevo.");
    });
}

async function unlockUser(userId) {
  const user = lastUsers.find((u) => Number(u.id) === userId);
  const userName = user ? user.display_name || user.email : `#${userId}`;

  const confirmed = await appConfirm(
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
      appAlert(`El usuario "${userName}" fue desbloqueado correctamente.`, { tone: "success" });
      loadUsers();
    })
    .catch((err) => {
      if (err.isSessionExpired) return;
      console.error("Error al desbloquear usuario:", err);
      appAlert(err.message || "No se pudo desbloquear el usuario. Intentá de nuevo.");
    });
}

async function softDeleteUser(userId) {
  const user = lastUsers.find((u) => Number(u.id) === userId);
  const userName = user ? user.display_name || user.email : `#${userId}`;

  const confirmed = await appConfirm(
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
      appAlert(`El usuario "${userName}" fue desactivado correctamente.`, { tone: "success" });
      // Recargar la lista manteniendo la página actual si sigue siendo válida.
      // Si la página actual quedó fuera de rango, el backend ajusta automáticamente.
      loadUsers();
    })
    .catch((err) => {
      if (err.isSessionExpired) return;
      console.error("Error al desactivar usuario:", err);
      appAlert(err.message || "No se pudo desactivar el usuario. Intentá de nuevo.");
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

  content.innerHTML = `<div style="text-align:center; color:var(--muted); padding:20px;"><span class="app-skeleton" role="status" aria-label="Cargando…"><span></span><span></span><span></span></span></div>`;
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
            <div style="display:flex; justify-content:space-between; align-items:baseline; gap:16px; padding:9px 0; border-bottom:1px solid var(--line);">
              <span style="flex:0 0 auto; max-width:55%; font-size:12.5px; font-weight:600; color:var(--muted); line-height:1.35;">${escapeHtml(label)}</span>
              <span style="flex:1; font-size:14px; color:var(--ink); text-align:right; word-break:break-word;">${escapeHtml(value)}</span>
            </div>
          `
        )
        .join("");
    })
    .catch((err) => {
      if (err.isSessionExpired) return;
      console.error("Error al ver usuario:", err);
      content.innerHTML = `<div style="text-align:center; color:var(--danger-500); padding:20px;">${escapeHtml(err.message)}</div>`;
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
    appAlert("No se pudo encontrar el usuario seleccionado.");
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
bindAdvancedFilter(orderingFieldFilter, () => {
  currentOrderingField = orderingFieldFilter.value;
  updateSortHeaders();
});
bindAdvancedFilter(orderingDirFilter, () => {
  currentOrderingDir = orderingDirFilter.value;
  updateSortHeaders();
});

// ORDEN POR ENCABEZADO: un clic ordena por esa columna, otro invierte el
// sentido y el tercero vuelve al orden normal. Las fechas y los pedidos
// arrancan por lo más reciente / lo más alto, que es lo que se busca. Es el
// mismo estado que "Ordenar por" de los filtros avanzados (se mantienen iguales).
const SORT_STARTS_DESC = new Set(["date_joined", "last_order_at", "orders_count", "last_login"]);

function updateSortHeaders() {
  document.querySelectorAll(".th-sort[data-sort]").forEach((button) => {
    const active = button.dataset.sort === currentOrderingField;
    button.dataset.dir = active ? currentOrderingDir : "";
    button.closest("th")?.setAttribute(
      "aria-sort",
      active ? (currentOrderingDir === "desc" ? "descending" : "ascending") : "none"
    );
  });
  if (orderingFieldFilter) orderingFieldFilter.value = currentOrderingField;
  if (orderingDirFilter) orderingDirFilter.value = currentOrderingDir;
}

document.querySelectorAll(".th-sort[data-sort]").forEach((button) => {
  button.addEventListener("click", () => {
    const field = button.dataset.sort;
    const firstDir = SORT_STARTS_DESC.has(field) ? "desc" : "asc";
    if (currentOrderingField !== field) {
      currentOrderingField = field;
      currentOrderingDir = firstDir;
    } else if (currentOrderingDir === firstDir) {
      currentOrderingDir = firstDir === "asc" ? "desc" : "asc";
    } else {
      currentOrderingField = "id";
      currentOrderingDir = "asc";
    }
    updateSortHeaders();
    currentPage = 1;
    loadUsers();
  });
});
updateSortHeaders();

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
    updateSortHeaders();

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
// Ahora es una ventana centrada: Esc la cierra, como a los diálogos.
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && panel.style.display === "block") {
    resetCreateForm();
    panel.style.display = "none";
  }
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
    window.Auth.saveSession({ ...stored, ...user });
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
    appAlert("Completá todos los campos obligatorios.", { tone: "info" });
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
    appAlert(err.message || (isEditing ? "No se pudo editar el usuario." : "No se pudo crear el usuario."));
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
  // Exportar tiene su permiso (migración 0029), aparte de ver la lista.
  const canExport = canUseUserPermission("users.export");
  ["exportUsersBtn", "exportUsersCsvBtn"].forEach((id) => {
    const button = document.getElementById(id);
    if (button) button.hidden = !canExport;
  });
  loadMyProfile();
  loadUsers();
  // Cargar dinámicamente TODOS los roles (básicos + personalizados).
  loadRoleOptions();
}

bootstrap();
