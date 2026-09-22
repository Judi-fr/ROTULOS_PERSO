// ---------------------------------------------------------------------------
// ROLES Y PERMISOS: administración exclusiva para ADMIN.
// (Antes era una sección de gestionuser.html; ver roles.html.)
// ---------------------------------------------------------------------------
const ROLES_URL = `${window.APP_CONFIG.API_BASE}/auth/roles/`;
const PERMISSIONS_URL = `${window.APP_CONFIG.API_BASE}/auth/permissions/`;

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
    // Recargar la lista de roles.
    rolesLoaded = false;
    await loadRoles();
    if (roles.length) {
      await loadRolePermissions(getRoleId(roles[0]));
    } else {
      setPermissionsLoading("No hay roles disponibles.");
    }
    showRolesMessage(`Rol "${role.label}" eliminado correctamente.`, "success");
  } catch (err) {
    if (err.isSessionExpired) return;
    console.error("Error al eliminar rol:", err);
    showRolesMessage(err.message || "No se pudo eliminar el rol.");
  }
}

document.getElementById("saveRolePermissions")
  ?.addEventListener("click", (event) => {
    event.preventDefault();
    saveRolePermissions();
  });
document.getElementById("createRoleBtn")
  ?.addEventListener("click", (event) => {
    event.preventDefault();
    createRole();
  });

// ---------------------------------------------------------------------------
// Esta pantalla es exclusiva de ADMIN. Sin ese modo, se redirige al
// dashboard (mismo criterio que el resto de las páginas de administración).
// ---------------------------------------------------------------------------
async function bootstrap() {
  if (!isAdminMode()) {
    window.location.replace("dashboard.html");
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

bootstrap();
