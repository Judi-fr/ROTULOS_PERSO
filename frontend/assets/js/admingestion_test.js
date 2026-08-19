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
const API_URL = "http://127.0.0.1:8000/api/v1/auth/users/";
const ME_URL = "http://127.0.0.1:8000/api/v1/auth/me/";

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
  return Boolean(u.can_manage_users || u.is_staff);
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
    alert("Tu sesión expiró. Volvé a iniciar sesión.");
    localStorage.removeItem("access");
    localStorage.removeItem("refresh");
    localStorage.removeItem("user");
    window.location.replace("index.html");
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

  users.forEach((u, i) => {
    const tr = document.createElement("tr");
    const role = u.role_label || "User";
    const statusKey = u.status_key || (u.is_active ? "active" : "inactive");
    const statusText = u.status_label || statusLabel[statusKey] || statusKey;
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
            <a href="#" class="edit-user" data-edit-id="${u.id}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/></svg>Edit</a>
            <div class="sep"></div>
            ${u.can_reactivate ? `<a href="#" class="reactivate-user" data-reactivate-id="${u.id}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/><path d="M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>Reactivate</a>` : ""}
            <a href="#" class="danger delete-user" data-delete-id="${u.id}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/></svg>Delete</a>
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
    const roleValue = roleKey.charAt(0).toUpperCase() + roleKey.slice(1);
    roleSelect.value = roleValue;
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

  // Activar el botón de perfil (click abre el modal de edición propia)
  const profileBtn = document.getElementById("profileBtn");
  if (profileBtn) {
    profileBtn.addEventListener("click", () => openProfilePanel(user));
  }
}

function openProfilePanel(user) {
  profileMode = true;
  editingUserId = null;

  const fullNameInput = document.getElementById("fullNameInput");
  const emailInput = document.getElementById("emailInput");
  const roleSelect = document.getElementById("roleSelect");
  const statusSelect = document.getElementById("statusSelect");
  const title = document.getElementById("userFormTitle");
  const submitBtn = document.getElementById("userFormSubmit");
  const passwordField = document.getElementById("passwordField");
  const passwordInput = document.getElementById("passwordInput");

  if (fullNameInput) fullNameInput.value = user.display_name || "";
  if (emailInput) emailInput.value = user.email || "";
  if (roleSelect) roleSelect.value = "Subscriber"; // solo lectura visual
  if (statusSelect) statusSelect.value = user.is_active ? "Active" : "Inactive";

  // Ocultar controles administrativos
  if (roleSelect) roleSelect.disabled = true;
  if (statusSelect) statusSelect.disabled = true;

  // Mostrar bloque de cambio de contraseña
  if (passwordField) {
    passwordField.style.display = "block";
    passwordField.querySelector("label").textContent = "Cambiar contraseña (opcional)";
  }
  if (passwordInput) {
    passwordInput.placeholder = "Nueva contraseña";
    passwordInput.required = false;
    passwordInput.type = "password";
  }

  if (title) title.textContent = "Mi Perfil";
  if (submitBtn) submitBtn.textContent = "Guardar Cambios";

  panel.style.display = "block";
}

async function loadMyProfile() {
  try {
    const response = await apiFetch(ME_URL);
    if (!response.ok) {
      throw new Error(`Error HTTP: ${response.status}`);
    }
    const user = await response.json();
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
  const newPassword = document.getElementById("passwordInput").value.trim();

  if (!email || !fullName) {
    alert("Completá tu nombre y email.");
    return;
  }

  const payload = { email, full_name: fullName };

  // Cambio de contraseña opcional
  if (newPassword) {
    const confirm = prompt("Confirmá tu nueva contraseña:");
    if (newPassword !== confirm) {
      alert("Las contraseñas no coinciden.");
      return;
    }
    const current = prompt("Ingresá tu contraseña actual:");
    payload.current_password = current;
    payload.new_password = newPassword;
    payload.confirm_new_password = newPassword;
  }

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
    // Actualizar sidebar y localStorage
    const stored = getCurrentUser();
    const updated = { ...stored, email: data.email || stored.email, display_name: data.display_name || fullName };
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
// 🚀 INICIO: decide modo ADMIN o modo USUARIO
// ---------------------------------------------------------------------------
function bootstrap() {
  const user = getCurrentUser();
  const adminMode = isAdminMode();

  if (adminMode) {
    document.getElementById("openCreatePanel").style.display = "";
    loadUsers();
  } else {
    hideAdminUI();
    renderProfilePanel(user);
    loadMyProfile();
  }
}

bootstrap();
