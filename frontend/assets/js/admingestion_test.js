const statusLabel = {
  active: "Active",
  inactive: "Inactive",
};
const roleClass = {
  Admin: "admin",
  Designer: "designer",
  Operator: "operator",
  Subscriber: "subscriber",
};

const tbody = document.getElementById("usersBody");

// Endpoint real del proyecto
const API_URL = "http://127.0.0.1:8000/api/v1/users/";
const ME_URL = "http://127.0.0.1:8000/api/v1/auth/me/";
const CHANGE_PASSWORD_URL = "http://127.0.0.1:8000/api/v1/auth/me/change-password/";
const ROLES_URL = "http://127.0.0.1:8000/api/v1/auth/roles/";
const PERMISSIONS_URL = "http://127.0.0.1:8000/api/v1/auth/permissions/";

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

  return response;
}

// Estado de búsqueda, filtros y paginación
let currentPage = 1;
let currentSearch = "";
let currentStatus = "all";
let currentRole = "all";
let lastPage = 1;
let lastUsers = [];
let editingUserId = null;

function buildQueryString() {
  const params = new URLSearchParams();
  if (currentSearch) params.set("search", currentSearch);
  if (currentStatus && currentStatus !== "all") params.set("status", currentStatus);
  if (currentRole && currentRole !== "all") params.set("role", currentRole);
  if (currentPage > 1) params.set("page", currentPage);
  const qs = params.toString();
  return qs ? `${API_URL}?${qs}` : API_URL;
}

async function loadUsers() {
  tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:20px;">Cargando usuarios...</td></tr>`;

  try {
    const response = await apiFetch(buildQueryString());

    if (!response.ok) {
      throw new Error(`Error HTTP: ${response.status}`);
    }

    const raw = await response.json();

    // La API devuelve { results: [...], pagination, summary }
    const users = Array.isArray(raw) ? raw : raw.results || [];
    lastUsers = users;
    const pagination = raw.pagination || null;

    if (users.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:20px;">No hay usuarios para mostrar.</td></tr>`;
    } else {
      renderUsers(users);
    }

    renderPagination(pagination);
    renderSummary(raw.summary);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar usuarios:", err);
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center; padding:20px; color:red;">
      No se pudieron cargar los usuarios. Intentá de nuevo más tarde.
    </td></tr>`;
  }
}

function renderUsers(users) {
  tbody.innerHTML = ""; // limpia el "Cargando..."
  const currentUser = getCurrentUser();
  const currentUserId = currentUser && Number(currentUser.id);
  const canEditUsers = canUseUserPermission("users.edit");
  const canDeactivateUsers = canUseUserPermission("users.deactivate");
  const canReactivateUsers = canUseUserPermission("users.reactivate");

  users.forEach((u, i) => {
    const tr = document.createElement("tr");
    const role = u.role_label || "User";
    const statusKey = u.status_key || (u.is_active ? "active" : "inactive");
    const statusText = u.status_label || statusLabel[statusKey] || statusKey;
    const isSelf = Number(u.id) === currentUserId;
    // El admin NO puede desactivarse/eliminarse a sí mismo (protección doble:
    // backend devuelve 403 y el frontend no muestra siquiera la opción).
    const deleteAction = !canDeactivateUsers
      ? ""
      : isSelf
      ? `<span class="self-protected" style="font-size:12px; color:#94a3b8;" title="No podés desactivar tu propia cuenta">No podés eliminarte</span>`
      : `<a href="#" class="danger delete-user" data-delete-id="${u.id}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>Delete</a>`;

    tr.innerHTML = `
      <td><input type="checkbox"></td>
      <td>
        <div class="user-cell">
          <img class="user-avatar" src="https://api.dicebear.com/7.x/avataaars/svg?seed=${u.email || u.id}" alt="">
          <span class="user-name">${u.display_name || u.email}</span>
        </div>
      </td>
      <td class="cell-muted">${u.email}</td>
      <td><span class="badge ${roleClass[role] || ""}">${role}</span></td>
      <td><span class="status ${statusKey}"><span class="dot"></span>${statusText}</span></td>
      <td class="cell-muted">${u.created_date || "-"}</td>
      <td class="actions-col">
        <div class="row-actions">
          <button class="more-btn" data-idx="${i}">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg>
          </button>
          <div class="dropdown" id="dd-${i}" style="display:none;">
            <a href="#" class="view-user" data-view-id="${u.id}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>View</a>
            ${canEditUsers ? `<a href="#" class="edit-user" data-edit-id="${u.id}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/></svg>Edit</a>` : ""}
            <div class="sep"></div>
            ${canReactivateUsers && u.can_reactivate ? `<a href="#" class="reactivate-user" data-reactivate-id="${u.id}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/><path d="M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>Reactivate</a>` : ""}
            ${deleteAction}
          </div>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });

  attachDropdownListeners();
}

function renderPagination(pagination) {
  const infoEl = document.getElementById("paginationInfo");
  const numbersEl = document.getElementById("pageNumbers");
  const controlsEl = document.getElementById("paginationControls");

  if (!pagination) {
    if (infoEl) infoEl.textContent = "Showing 0 users";
    if (numbersEl) numbersEl.innerHTML = "";
    return;
  }

  const { count, from, to, total_pages, page, has_previous, has_next, previous_page, next_page } = pagination;
  lastPage = total_pages || 1;

  if (infoEl) {
    infoEl.textContent = count > 0
      ? `Showing ${from} to ${to} of ${count} users`
      : "Showing 0 users";
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
  if (totalEl) totalEl.textContent = summary.total ?? "—";
  if (activeEl) activeEl.textContent = summary.active ?? "—";
  if (inactiveEl) inactiveEl.textContent = summary.inactive ?? "—";
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
      const role = user.role_label || "User";
      const statusKey = user.status_key || (user.is_active ? "active" : "inactive");
      const statusText = user.status_label || statusLabel[statusKey] || statusKey;

      const rows = [
        ["ID", user.id ?? "-"],
        ["Name", user.display_name || "-"],
        ["Email", user.email || "-"],
        ["Role", role],
        ["Status", statusText],
        ["Created", user.created_date || "-"],
      ];

      content.innerHTML = rows
        .map(
          ([label, value]) => `
            <div style="display:flex; justify-content:space-between; padding:10px 0; border-bottom:1px solid #e7eaf1;">
              <span style="font-size:13px; font-weight:600; color:#64748b;">${label}</span>
              <span style="font-size:14px; color:#0f172a; text-align:right;">${value}</span>
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
  if (!roleSelect && !roleFilter) return;

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
      roleSelect.innerHTML = '<option value="">Select role</option>';
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
      roleFilter.innerHTML = '<option value="all">All</option>';
      options.forEach(({ key, label }) => {
        const opt = document.createElement("option");
        opt.value = key;
        opt.textContent = label;
        roleFilter.appendChild(opt);
      });
      if (current) roleFilter.value = current;
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
  if (title) title.textContent = "Create User";
  if (submitBtn) submitBtn.textContent = "Create User";

  // En modo creación el campo Password está visible y es obligatorio.
  const passwordField = document.getElementById("passwordField");
  const passwordInput = document.getElementById("passwordInput");
  if (passwordField) passwordField.style.display = "block";
  if (passwordInput) {
    passwordInput.value = "";
    passwordInput.required = true;
  }
  // La sección de cambio de contraseña es solo del perfil propio (modo USER).
  hidePasswordSection();
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

  // El backend devuelve role_key / status_key en minúsculas.
  // El <select> usa valores con mayúscula inicial ("Admin", "Active"),
  // así que mapeamos la primera letra a mayúscula para matchear la option.
  const roleKey = user.role_key || "subscriber";
  const statusKey = user.status_key || (user.is_active ? "active" : "inactive");

  if (roleSelect) {
    // Los valores del <select> son los nombres de Group en minúsculas
    // (cargados dinámicamente desde /api/v1/auth/roles/).
    roleSelect.value = roleKey;
  }
  if (statusSelect) {
    const statusValue = statusKey.charAt(0).toUpperCase() + statusKey.slice(1);
    statusSelect.value = statusValue;
  }

  // Cambios visuales mínimos para indicar modo edición
  const title = document.getElementById("userFormTitle");
  const submitBtn = document.getElementById("userFormSubmit");
  if (title) title.textContent = "Edit User";
  if (submitBtn) submitBtn.textContent = "Save Changes";

  // En modo edición NUNCA se muestra ni se precarga la contraseña existente.
  const passwordField = document.getElementById("passwordField");
  const passwordInput = document.getElementById("passwordInput");
  if (passwordField) passwordField.style.display = "none";
  if (passwordInput) {
    passwordInput.value = "";
    passwordInput.required = false;
  }
  // La sección de cambio de contraseña es solo del perfil propio (modo USER).
  hidePasswordSection();

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

// Crear/Editar usuario / Perfil propio: el submit se maneja de forma
// unificada más abajo (handleAdminSubmit / saveMyProfile según el modo).
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

    // 3. Redirigir a la pantalla de logout del proyecto.
    window.location.replace("logoutpage.html");
  });
}

// ---------------------------------------------------------------------------
// PERFIL PROPIO: panel de usuario (reutiliza el mismo form visual del admin)
// ---------------------------------------------------------------------------
let profileMode = false; // true = editando perfil propio (/me/), false = admin edit/{id}

function hideAdminUI() {
  const adminSelectors = [
    "#openCreatePanel",
    "#navRoles",
    ".table-toolbar",
    "table",
    ".pagination-bar",
    ".stats",
  ];
  adminSelectors.forEach((sel) => {
    const el = document.querySelector(sel);
    if (el) el.style.display = "none";
  });
  const topbarTitle = document.querySelector(".topbar h1");
  const topbarSub = document.querySelector(".topbar p");
  if (topbarTitle) topbarTitle.textContent = "Mi Perfil";
  if (topbarSub) topbarSub.textContent = "Consulta y edita tus datos personales.";
}

function renderProfilePanel(user) {
  const nameEl = document.getElementById("profileName");
  const emailEl = document.getElementById("profileEmail");
  const avatarEl = document.getElementById("profileAvatar");
  if (nameEl) nameEl.textContent = user.display_name || user.email || "Usuario";
  if (emailEl) emailEl.textContent = user.email || "—";
  if (avatarEl) {
    avatarEl.src = `https://api.dicebear.com/7.x/avataaars/svg?seed=${encodeURIComponent(user.email || user.id || "user")}`;
  }

  // Activar el botón de perfil (click abre la vista Settings).
  // Se usa onclick (no addEventListener) para no acumular listeners cuando
  // renderProfilePanel se llama varias veces (bootstrap + loadMyProfile).
  const profileBtn = document.getElementById("profileBtn");
  if (profileBtn) {
    profileBtn.onclick = () => openSettings();
  }
}

function openProfilePanel(user) {
  openSettings(); // Redirigir al nuevo panel Settings
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

// Crea (una sola vez) la sección de cambio de contraseña dentro del panel de
// perfil. Se inserta después de los campos de datos y antes de las acciones.
function ensurePasswordSection() {
  if (document.getElementById("profilePasswordSection")) return;

  const panelEl = document.getElementById("createPanel");
  const actions = panelEl && panelEl.querySelector(".create-panel-actions");
  if (!panelEl || !actions) return;

  const div = document.createElement("div");
  div.id = "profilePasswordSection";
  div.style.cssText = "margin-bottom: 4px;";
  div.innerHTML = `
    <hr style="margin: 16px 0; border-color: var(--border, #e0e0e0);">
    <h4 style="margin: 0 0 12px; font-size: 0.9rem; color: var(--text-secondary, #666);">
      Cambiar contraseña
    </h4>
    <div class="form-group">
      <label>Contraseña actual</label>
      <input type="password" id="currentPassword" placeholder="Tu contraseña actual" autocomplete="current-password">
    </div>
    <div class="form-group">
      <label>Nueva contraseña</label>
      <input type="password" id="newPassword" placeholder="Mínimo 8 caracteres" autocomplete="new-password">
    </div>
    <div class="form-group">
      <label>Confirmar nueva contraseña</label>
      <input type="password" id="confirmPassword" placeholder="Repetir nueva contraseña" autocomplete="new-password">
    </div>
    <p id="passwordMsg" style="display:none; font-size: 0.85rem; margin: 4px 0 0;"></p>
    <button type="button" onclick="saveMyPassword()" class="btn btn-outline"
            style="margin-top: 8px;">
      Cambiar contraseña
    </button>
  `;
  actions.before(div);
}

function hidePasswordSection() {
  const section = document.getElementById("profilePasswordSection");
  if (section) section.style.display = "none";
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
    renderProfilePanel(user);
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al cargar perfil:", err);
  }
}

function resetProfileFields() {
  const roleSelect = document.getElementById("roleSelect");
  const statusSelect = document.getElementById("statusSelect");
  const passwordField = document.getElementById("passwordField");
  const passwordInput = document.getElementById("passwordInput");
  const fullNameInput = document.getElementById("fullNameInput");
  const emailInput = document.getElementById("emailInput");

  if (roleSelect) roleSelect.disabled = false;
  if (statusSelect) statusSelect.disabled = false;
  if (passwordField) passwordField.style.display = "none";
  if (passwordField) passwordField.querySelector("label").textContent = "Password";
  if (passwordInput) {
    passwordInput.required = false;
    passwordInput.placeholder = "Enter password";
    passwordInput.type = "password";
  }
  if (fullNameInput) fullNameInput.value = "";
  if (emailInput) emailInput.value = "";
  profileMode = false;
  hidePasswordSection();
}

// Hook del submit del form para perfil propio (sobrescribe al de admin cuando aplica)
const originalSubmitListener = createUserForm ? createUserForm.onSubmit : null;
if (createUserForm) {
  createUserForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (profileMode) {
      await saveMyProfile();
    } else {
      // Delegar al handler existente (admin) ya registrado abajo
      const evt = new Event("submit", { cancelable: true });
      // Invoke the admin handler via the original listener chain
      await handleAdminSubmit(e);
    }
  });
}

async function saveMyProfile() {
  const email = document.getElementById("emailInput").value.trim();
  const fullName = document.getElementById("fullNameInput").value.trim();

  if (!email || !fullName) {
    alert("Completá tu nombre y email.");
    return;
  }

  // El endpoint /me/ solo acepta first_name/last_name (el email es read-only).
  const parts = fullName.split(/\s+/);
  const payload = {
    first_name: parts[0] || "",
    last_name: parts.slice(1).join(" ") || "",
  };

  const submitBtn = document.getElementById("userFormSubmit");
  const originalText = submitBtn.textContent;
  submitBtn.disabled = true;
  submitBtn.textContent = "Guardando...";

  try {
    const response = await apiFetch(ME_URL, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
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

    alert("Perfil actualizado correctamente.");
    panel.style.display = "none";
    resetProfileFields();
    // Actualizar sidebar y localStorage con los datos nuevos del backend.
    const stored = getCurrentUser();
    const updated = {
      ...stored,
      email: data.email || stored.email,
      first_name: data.first_name || stored.first_name || "",
      last_name: data.last_name || stored.last_name || "",
      display_name: data.display_name || fullName,
    };
    localStorage.setItem("user", JSON.stringify(updated));
    loadMyProfile();
  } catch (err) {
    if (err.isSessionExpired) return;
    alert(err.message || "No se pudo actualizar tu perfil.");
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = originalText;
  }
}

async function saveMyPassword() {
  const current = document.getElementById("currentPassword")?.value?.trim();
  const nuevo   = document.getElementById("newPassword")?.value?.trim();
  const confirm = document.getElementById("confirmPassword")?.value?.trim();
  const msg     = document.getElementById("passwordMsg");

  const showMsg = (text, ok) => {
    if (!msg) return;
    msg.textContent = text;
    msg.style.color   = ok ? "green" : "red";
    msg.style.display = "block";
  };

  if (!current || !nuevo || !confirm) {
    showMsg("Completá los tres campos para cambiar la contraseña.", false);
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

    const data = await res.json().catch(() => ({}));

    if (res.ok) {
      showMsg("Contraseña actualizada correctamente.", true);
      document.getElementById("currentPassword").value = "";
      document.getElementById("newPassword").value     = "";
      document.getElementById("confirmPassword").value = "";
    } else if (res.status === 401) {
      showMsg("Sesión expirada. Volvé a iniciar sesión.", false);
    } else {
      const err =
        data?.current_password?.[0] ||
        data?.confirm_password?.[0] ||
        data?.new_password?.[0] ||
        data?.detail ||
        "Error al cambiar la contraseña.";
      showMsg(err, false);
    }
  } catch {
    showMsg("Error de red. Verificá tu conexión.", false);
  }
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
  const password = isEditing ? "" : document.getElementById("passwordInput").value.trim();
  if (!isEditing && !password) {
    alert("Ingresá una contraseña para el nuevo usuario.");
    return;
  }

  const submitBtn = document.getElementById("userFormSubmit");
  const originalText = submitBtn.textContent;
  submitBtn.disabled = true;
  submitBtn.textContent = editingUserId ? "Guardando..." : "Creando...";

  const url = isEditing ? `${API_URL}${editingUserId}/` : API_URL;
  const method = isEditing ? "PATCH" : "POST";

  try {
    const response = await apiFetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(isEditing ? { email, full_name: fullName, role, status } : { email, full_name: fullName, role, status, password }),
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
  setPermissionsLoading("Loading permissions...");
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
  setRolesLoading("Loading roles...");
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
  setPermissionsLoading("Loading permissions...");
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

async function saveRolePermissions() {
  if (!isAdminMode() || !selectedRole) return;
  const button = document.getElementById("saveRolePermissions");
  const selectedPermissions = Array.from(
    document.querySelectorAll('#rolePermissionsContent input[type="checkbox"]:checked')
  ).map((checkbox) => checkbox.value);
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Saving...";
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
    openSettings();
  });

// profileBtn (sidebar footer) → también abre Settings
document.getElementById("profileBtn")
  ?.addEventListener("click", () => openSettings());

// Botones dentro de Settings
document.getElementById("settingsSaveProfile")
  ?.addEventListener("click", settingsSaveProfile);
document.getElementById("settingsSavePassword")
  ?.addEventListener("click", settingsSavePassword);

// Los demás ítems cierran las vistas mutuamente excluyentes.
document.querySelectorAll(".nav a:not(#navSettings):not(#navRoles)").forEach((a) => {
  a.addEventListener("click", () => {
    closeSettings();
    closeRoles();
  });
});

// ---------------------------------------------------------------------------
// 🚀 INICIO: decide modo ADMIN o modo USUARIO
// ---------------------------------------------------------------------------
function bootstrap() {
  const user = getCurrentUser();
  const adminMode = isAdminMode();

  if (canViewUsers()) {
    document.getElementById("openCreatePanel").style.display = canUseUserPermission("users.create") ? "" : "none";
    if (!adminMode) document.getElementById("navRoles").style.display = "none";
    // El sidebar muestra el perfil del admin autenticado (no "Cargando...").
    // Primero se pinta con lo que haya en localStorage y luego se refresca
    // con los datos reales de /me/.
    renderProfilePanel(user);
    loadMyProfile();
    loadUsers();
    // Cargar dinámicamente TODOS los roles (básicos + personalizados).
    loadRoleOptions();
  } else {
    hideAdminUI();
    renderProfilePanel(user);
    loadMyProfile();
  }
}

bootstrap();
