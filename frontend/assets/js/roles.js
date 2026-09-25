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
// La pantalla NO tiene lista de permisos: los muestra TODOS los que devuelve
// /auth/permissions/, agrupados por la categoría que trae cada uno y con el
// nombre en español que ya guarda el backend (RolePermission.name).
//
// Antes había acá una lista blanca de ocho permisos y el catálogo de la API
// se filtraba contra ella: el backend controlaba 41 permisos y un admin solo
// podía asignar 8. Los otros 33 existían, se aplicaban, y no había forma de
// tocarlos desde ningún lado. Armar la pantalla con lo que manda el servidor
// evita que eso vuelva a pasar: un permiso nuevo en el backend aparece acá
// solo, sin tocar este archivo.

// Título de cada categoría. Una categoría que no esté acá igual se muestra,
// con su nombre crudo en mayúsculas: preferible una sección fea a un permiso
// invisible.
const CATEGORY_LABELS = {
  perfil: "PERFIL PROPIO",
  users: "USUARIOS",
  orders: "PEDIDOS Y ENVÍOS",
  labels: "RÓTULOS",
  plantillas: "PLANTILLAS Y VARIABLES",
  documents: "DOCUMENTOS",
  processing: "IMPORTACIÓN POR FOTO",
  support: "SOPORTE",
  integrations: "INTEGRACIONES",
  audit: "AUDITORÍA",
};

// Orden de las secciones: de lo más cotidiano a lo más administrativo. Lo que
// no esté listado va al final, alfabético.
const CATEGORY_ORDER = [
  "perfil",
  "orders",
  "labels",
  "plantillas",
  "documents",
  "processing",
  "support",
  "users",
  "integrations",
  "audit",
];

// Algunos permisos comparten categoría en el backend pero son otra cosa para
// quien los asigna: el perfil propio no es administrar usuarios, y las
// plantillas no son los rótulos. Se reagrupan por prefijo de la clave, que es
// un dato y no una lista de permisos, así que sigue sin haber nada que
// actualizar cuando se agrega uno.
const CATEGORY_BY_PREFIX = [
  ["users.me.", "perfil"],
  ["plantillas.", "plantillas"],
  ["variables.", "plantillas"],
];

const MANAGEABLE_ROLE_KEYS = new Set(ROLE_DEFINITIONS.map((role) => role.key));

// El catálogo llega a veces como strings y a veces como objetos (ver
// loadPermissionCatalog): estas tres funciones son el único lugar que sabe
// de esa diferencia.
function permissionCode(permission) {
  if (typeof permission === "string") return permission;
  return permission.code || permission.key || permission.name;
}

function permissionLabel(permission) {
  if (typeof permission === "string") return permission;
  // Sin nombre se muestra la clave: un permiso sin etiqueta se sigue pudiendo
  // asignar, que es lo que importa.
  return permission.name || permissionCode(permission);
}

function permissionCategory(permission) {
  const code = permissionCode(permission) || "";
  const porPrefijo = CATEGORY_BY_PREFIX.find(([prefijo]) => code.startsWith(prefijo));
  if (porPrefijo) return porPrefijo[1];
  if (typeof permission !== "string" && permission.category) return permission.category;
  // Sin categoría, la primera parte de la clave ("orders.create" -> "orders").
  return code.split(".")[0] || "otros";
}
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

  const assigned = new Set(permissionCodes.map(permissionCode));
  // Agrupar el catálogo por categoría, conservando el orden de CATEGORY_ORDER
  // y mandando al final, alfabéticamente, cualquier categoría desconocida.
  const porCategoria = new Map();
  permissionCatalog.forEach((permission) => {
    const categoria = permissionCategory(permission);
    if (!porCategoria.has(categoria)) porCategoria.set(categoria, []);
    porCategoria.get(categoria).push(permission);
  });

  const categorias = [...porCategoria.keys()].sort((a, b) => {
    const ia = CATEGORY_ORDER.indexOf(a);
    const ib = CATEGORY_ORDER.indexOf(b);
    if (ia !== -1 && ib !== -1) return ia - ib;
    if (ia !== -1) return -1;
    if (ib !== -1) return 1;
    return a.localeCompare(b, "es");
  });

  content.innerHTML = "";
  categorias.forEach((categoria) => {
    const groupEl = document.createElement("section");
    groupEl.className = "permission-group";
    const title = document.createElement("h3");
    title.textContent = CATEGORY_LABELS[categoria] || categoria.toUpperCase();
    groupEl.appendChild(title);

    const items = porCategoria
      .get(categoria)
      .slice()
      .sort((a, b) => permissionLabel(a).localeCompare(permissionLabel(b), "es"));

    items.forEach((permission) => {
      const code = permissionCode(permission);
      const option = document.createElement("label");
      option.className = "permission-option";
      // La clave va en el title: dos permisos pueden leerse parecido y quien
      // administra roles necesita saber cuál es cuál.
      option.title = code;
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = code;
      checkbox.checked = assigned.has(code);
      const text = document.createElement("span");
      text.textContent = permissionLabel(permission);
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

  // Sin filtro: se muestra TODO lo que el backend dice que existe. Lo único
  // que se descarta es una entrada sin clave, que no se podría asignar.
  const catalog = Array.isArray(data) ? data : data.results || data.permissions || [];
  permissionCatalog = catalog.filter((permission) => Boolean(permissionCode(permission)));
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
